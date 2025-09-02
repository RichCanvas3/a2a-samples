import logging
import os
from typing import Any, Optional

from web3 import Web3
from web3.exceptions import TimeExhausted


logger = logging.getLogger(__name__)
if not logger.hasHandlers():
    logging.basicConfig(level=logging.INFO)


class Erc8004Adapter:
    """Lightweight, optional ERC-8004 integration layer.

    This class is intentionally minimal and non-opinionated. It reads config
    from environment variables and provides no-op fallbacks so the agent runs
    without blockchain connectivity if desired.

    Expected environment variables (optional):
    - ERC8004_ENABLED=true|false
    - ERC8004_RPC_URL

    - ERC8004_IDENTITY_REGISTRY
    - ERC8004_REPUTATION_REGISTRY
    """

    def __init__(self, private_key: Optional[str] = None, rpc_url: Optional[str] = None) -> None:
        self.enabled = os.getenv('ERC8004_ENABLED', 'false').lower() == 'true'
        self.rpc_url = rpc_url or os.getenv('ERC8004_RPC_URL')
        self.identity_registry = os.getenv('ERC8004_IDENTITY_REGISTRY')
        self.reputation_registry = os.getenv('ERC8004_REPUTATION_REGISTRY')
        self.agent_id: Optional[str] = None
        self._deployment_path = os.getenv('ERC8004_DEPLOYMENT_FILE', 'deployment.json')

        if self.enabled:
            logger.info('ERC-8004 adapter enabled.')
        else:
            logger.info('ERC-8004 adapter disabled. Running without on-chain writes.')

        self._w3: Web3 | None = None
        self._tx_timeout_sec = int(os.getenv('ERC8004_TX_TIMEOUT_SEC', '180'))
        # Initialize Web3 if RPC is available, even without a private key (read-only ops)
        if self.rpc_url:
            try:
                rpc_timeout = int(os.getenv('ERC8004_RPC_TIMEOUT_SEC', '20'))
                self._w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": rpc_timeout}))
                logger.info('ERC-8004 Web3 client initialized (read-only)')
            except Exception as e:
                logger.warning('ERC-8004: failed to init Web3 client: %s', e)
                self._w3 = None

        # Lazy-loaded contracts
        self._identity_contract = None
        self._reputation_contract = None
        # Attempt to hydrate contract addresses from deployment.json if env not set
        if self.enabled and not self.identity_registry:
            self._load_contract_addresses_from_deployment()

    def is_enabled(self) -> bool:
        return self.enabled

    def ensure_identity(self, agent_name: str, agent_domain: Optional[str] = None, signing_private_key: Optional[str] = None) -> None:
        """Ensure this agent has an identity.

        If ERC-8004 is enabled and registry is configured, attempts to register
        the agent if an agent_id is not already provided. Prefers IdentityRegistry
        ABI with `newAgent(string,address)` like the example repo, falling back to
        a minimal `register(string)` if needed. Domain is taken from param or
        ENV (ERC8004_AGENT_DOMAIN/APP_URL).
        Falls back to logging if configuration is incomplete.
        """
        if not self.enabled:
            return
        if not (self._w3 and self.identity_registry):
            logger.info('ERC-8004: ensure_identity skipped (missing Web3 or registry).')
            return

        try:
            # Try IdentityRegistry.newAgent(agentDomain,address)
            domain = agent_domain or os.getenv('ERC8004_AGENT_DOMAIN') or os.getenv('APP_URL') or agent_name

            identity = self._get_identity_contract()

            if identity is not None:
                if not signing_private_key:
                    logger.info('ERC-8004: ensure_identity skipped (no signing key provided 1).')
                    return
                acct_addr = self._w3.eth.account.from_key(signing_private_key).address
                logger.info('find agent by acct_addr: %s', acct_addr)
                # Pre-check: resolve existing registration by address
                try:
                    agent_info = identity.functions.resolveByAddress(acct_addr).call()
                    if agent_info and agent_info[0] and int(agent_info[0]) > 0:
                        self.agent_id = str(int(agent_info[0]))
                        logger.info('find agent by agent info: %s', agent_info)
                        logger.info('ERC-8004: agent already registered id=%s', self.agent_id)
                        return
                except Exception:
                    pass

                logger.info("Create New Agent: " + domain + ", " + acct_addr)
                fn = identity.functions.newAgent(domain, acct_addr)

                # Optional registration fee (default 0.0 if unset)
                fee_eth = float(os.getenv('ERC8004_REGISTRATION_FEE_ETH', '0.0'))
                value = self._w3.to_wei(fee_eth, 'ether') if fee_eth > 0 else 0
                # Gas/fees: estimate and apply safety margin + minimums; allow env overrides
                gas_est = None
                try:
                    gas_est = fn.estimate_gas({'from': acct_addr, 'value': value})
                except Exception:
                    gas_est = 300000
                gas_mult = float(os.getenv('ERC8004_GAS_MULT', '1.5'))
                min_gas = int(os.getenv('ERC8004_MIN_GAS', '500000'))
                gas_limit = max(int(gas_est * gas_mult), min_gas)

                gas_price = self._w3.eth.gas_price
                gas_price_mult = float(os.getenv('ERC8004_GAS_PRICE_MULT', '1.2'))
                try:
                    override_gwei = os.getenv('ERC8004_GAS_PRICE_MULT')
                    if override_gwei:
                        gas_price = self._w3.to_wei(float(override_gwei), 'gwei')
                    else:
                        gas_price = int(gas_price * gas_price_mult)
                except Exception:
                    pass
                tx = fn.build_transaction(
                    {
                        'from': acct_addr,
                        'nonce': self._w3.eth.get_transaction_count(acct_addr, 'pending'),
                        'gas': gas_limit,
                        'gasPrice': gas_price,
                        'value': value,
                    }
                )
                logger.info('ERC-8004: newAgent gas_limit=%s gas_price=%s wei (est=%s)', gas_limit, gas_price, gas_est)
                signed = self._w3.eth.account.sign_transaction(tx, signing_private_key)
                tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
                try:
                    receipt = self._w3.eth.wait_for_transaction_receipt(
                        tx_hash, timeout=self._tx_timeout_sec
                    )
                except TimeExhausted:
                    # One last attempt to fetch receipt without waiting
                    receipt = self._w3.eth.get_transaction_receipt(tx_hash)
                logger.info('ERC-8004: newAgent tx mined: %s (status=%s)', tx_hash.hex(), receipt.status)
                if getattr(receipt, 'status', 0) != 1:
                    self._log_tx_failure_details(tx_hash, receipt)
                    return

                # Try Approach 1: parse AgentRegistered events
                agent_id_val = None
                try:
                    logs = identity.events.AgentRegistered().process_receipt(receipt)
                    if logs:
                        agent_id_val = logs[0]['args'].get('agentId')
                except Exception as e:
                    logger.debug('ERC-8004: could not parse AgentRegistered logs: %s', e)

                # Approach 2: resolve by address with small retries
                if agent_id_val is None:
                    try:
                        import time as _time
                        for attempt in range(3):
                            try:
                                if attempt:
                                    _time.sleep(0.5)
                                agent_info = identity.functions.resolveByAddress(acct_addr).call()
                                if agent_info and agent_info[0] and int(agent_info[0]) > 0:
                                    agent_id_val = int(agent_info[0])
                                    break
                            except Exception as e:
                                if attempt == 2:
                                    logger.debug('ERC-8004: resolveByAddress failed: %s', e)
                    except Exception:
                        pass

                if agent_id_val is not None:
                    self.agent_id = str(agent_id_val)
                    logger.info('ERC-8004: resolved agent id=%s', self.agent_id)
                else:
                    # Last resort marker
                    self.agent_id = 'onchain'
                return

            # Fallback: minimal register(name) if IdentityRegistry ABI is unavailable
            abi_min = [
                {
                    "inputs": [{"internalType": "string", "name": "name", "type": "string"}],
                    "name": "register",
                    "outputs": [{"internalType": "uint256", "name": "id", "type": "uint256"}],
                    "stateMutability": "nonpayable",
                    "type": "function",
                }
            ]
            contract = self._w3.eth.contract(address=self.identity_registry, abi=abi_min)
            if not signing_private_key:
                logger.info('ERC-8004: ensure_identity skipped (no signing key provided 2).')
                return
            acct_addr = self._w3.eth.account.from_key(signing_private_key).address
            # Fallback gas config
            gas_mult = float(os.getenv('ERC8004_GAS_MULT', '1.5'))
            min_gas = int(os.getenv('ERC8004_MIN_GAS', '500000'))
            gas_price = self._w3.eth.gas_price
            gas_price_mult = float(os.getenv('ERC8004_GAS_PRICE_MULT', '1.2'))
            try:
                override_gwei = os.getenv('ERC8004_GAS_PRICE_MULT')
                if override_gwei:
                    gas_price = self._w3.to_wei(float(override_gwei), 'gwei')
                else:
                    gas_price = int(gas_price * gas_price_mult)
            except Exception:
                pass
            gas_limit = max(int(300000 * gas_mult), min_gas)

            tx = contract.functions.register(agent_name).build_transaction(
                {
                    'from': acct_addr,
                    'nonce': self._w3.eth.get_transaction_count(acct_addr, 'pending'),
                    'gas': gas_limit,
                    'gasPrice': gas_price,
                }
            )
            logger.info('ERC-8004: register gas_limit=%s gas_price=%s wei', gas_limit, gas_price)
            signed = self._w3.eth.account.sign_transaction(tx, signing_private_key)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            try:
                receipt = self._w3.eth.wait_for_transaction_receipt(
                    tx_hash, timeout=self._tx_timeout_sec
                )
            except TimeExhausted:
                receipt = self._w3.eth.get_transaction_receipt(tx_hash)
            logger.info('ERC-8004: register tx mined: %s (status=%s)', tx_hash.hex(), receipt.status)
            if getattr(receipt, 'status', 0) != 1:
                self._log_tx_failure_details(tx_hash, receipt)
                return

            # Try to extract agent_id even in fallback mode if we can load full ABI
            agent_id_val = None
            identity_full = self._get_identity_contract()
            if identity_full is not None:
                # Approach 1: parse AgentRegistered events
                try:
                    logs = identity_full.events.AgentRegistered().process_receipt(receipt)
                    if logs:
                        agent_id_val = logs[0]['args'].get('agentId')
                except Exception:
                    pass
                # Approach 2: resolve by address
                if agent_id_val is None:
                    try:
                        acct_addr = self._w3.eth.account.from_key(self.private_key).address
                        agent_info = identity_full.functions.resolveByAddress(acct_addr).call()
                        if agent_info and agent_info[0] and int(agent_info[0]) > 0:
                            agent_id_val = int(agent_info[0])
                    except Exception:
                        pass
            if agent_id_val is not None:
                self.agent_id = str(agent_id_val)
                logger.info('ERC-8004: agent_id set=%s', self.agent_id)
            else:
                # Leave marker if we couldn't resolve
                self.agent_id = 'onchain'
        except Exception as e:
            logger.warning('ERC-8004: ensure_identity failed: %s', e)

    # ---- helpers ----
    def _log_tx_failure_details(self, tx_hash, receipt) -> None:
        try:
            tx = self._w3.eth.get_transaction(tx_hash)
            logger.warning(
                'ERC-8004: tx revert. hash=%s block=%s gasUsed=%s to=%s',
                tx_hash.hex(), getattr(receipt, 'blockNumber', None), getattr(receipt, 'gasUsed', None), tx.to,
            )
            # Try to extract revert reason via eth_call simulation
            try:
                self._w3.eth.call(
                    {
                        'to': tx.to,
                        'from': tx['from'],
                        'data': tx['input'],
                        'value': tx['value'],
                    },
                    block_identifier=getattr(receipt, 'blockNumber', 'latest'),
                )
            except Exception as e:  # Provider raises here with revert info
                msg = str(e)
                # Best-effort slice of revert reason
                marker = 'execution reverted'
                reason = msg[msg.find(marker):] if marker in msg else msg
                logger.warning('ERC-8004: revert reason: %s', reason)
        except Exception as e:
            logger.debug('ERC-8004: failed to log tx failure details: %s', e)

    def _load_contract_addresses_from_deployment(self) -> None:
        try:
            import json
            with open(self._deployment_path, 'r') as f:
                deployment = json.load(f)
            contracts = deployment.get('contracts', {})
            self.identity_registry = self.identity_registry or contracts.get('identity_registry')
            self.reputation_registry = self.reputation_registry or contracts.get('reputation_registry')
        except Exception as e:
            logger.debug('ERC-8004: could not load deployment.json: %s', e)

    def _load_contract_abi(self, contract_name: str) -> Optional[list]:
        try:
            import json
            abi_path = f"contracts/out/{contract_name}.sol/{contract_name}.json"
            with open(abi_path, 'r') as f:
                artifact = json.load(f)
                return artifact.get('abi')
        except Exception as e:
            logger.info('ERC-8004: failed to load ABI for %s: %s', contract_name, e)
            return None

    def _get_identity_contract(self):
        logger.debug('ERC-8004: get identity contract: %s', self.identity_registry)
        if self._identity_contract is not None:
            logger.debug('ERC-8004: found identity contract: %s', self._identity_contract)
            return self._identity_contract
        if not (self._w3 and self.identity_registry):
            logger.debug('ERC-8004: no web3 or identity registry')
            return None
        abi = self._load_contract_abi('IdentityRegistry')
        if not abi:
            logger.debug('ERC-8004: no abi identity registry')
            return None
        try:
            self._identity_contract = self._w3.eth.contract(address=self.identity_registry, abi=abi)
            logger.debug('ERC-8004: found identity contract 2: %s', self._identity_contract)
            return self._identity_contract
        except Exception as e:
            logger.debug('ERC-8004: could not init IdentityRegistry contract: %s', e)
            return None

    def _get_reputation_contract(self):
        logger.debug('ERC-8004: get reputation contract: %s', self.reputation_registry)
        if self._reputation_contract is not None:
            return self._reputation_contract
        if not (self._w3 and self.reputation_registry):
            logger.debug('ERC-8004: no web3 or reputation registry')
            return None
        abi = self._load_contract_abi('ReputationRegistry')
        if not abi:
            logger.debug('ERC-8004: no abi reputation registry')
            return None
        try:
            self._reputation_contract = self._w3.eth.contract(address=self.reputation_registry, abi=abi)
            return self._reputation_contract
        except Exception as e:
            logger.debug('ERC-8004: could not init ReputationRegistry contract: %s', e)
            return None




    def authorize_feedback_from_client(
        self,
        client_agent_id: int,
        server_agent_id: int,
        server_agent_domain: Optional[str] = None,
        signing_private_key: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Authorize a client agent to provide feedback for this server agent.

        Returns details dict on success: {tx_hash, feedback_auth_id?, client_agent_id, server_agent_id, client_address?}
        Returns None on no-op/failure.
        """
        if not self.enabled:
            return None
        if not (self._w3 and self.reputation_registry):
            logger.info('ERC-8004: authorize_feedback skipped (missing Web3 or reputation registry).')
            return None

        # Ensure we know our own agent_id (only if caller didn't provide one)
        # Caller must provide the server agent id explicitly
        if server_agent_id is None:  # type: ignore[unreachable]
            logger.info('ERC-8004: authorize_feedback skipped (server agent_id not provided).')
            return None

        contract = self._get_reputation_contract()
        if contract is None:
            logger.info('ERC-8004: authorize_feedback skipped (no reputation contract).')
            return None

        try:
            # Always sign with the provided Assistant key per requirement
            pk = signing_private_key
            if not pk:
                logger.info('ERC-8004: authorize_feedback skipped (no private key).')
                return None
            acct_addr = self._w3.eth.account.from_key(pk).address
            server_agent_id_int = int(server_agent_id)
            client_agent_id_int = int(client_agent_id)

            # Try multiple function names for compatibility
            fn = None
            logger.info('ERC-8004: authorize_feedback client_agent_id: %s, server_agent_id: %s', client_agent_id_int, server_agent_id_int)
            for fn_name in ('acceptFeedback', 'authorizeFeedback', 'allowFeedback'):
                try:
                    fn = getattr(contract.functions, fn_name)(client_agent_id_int, server_agent_id_int)
                    break
                except Exception:
                    fn = None
            if fn is None:
                logger.info('ERC-8004: Feedback authorization function not found in ABI.')
                return None

            # Gas/fees for authorize_feedback: estimate with safety margin and allow env overrides
            gas_est = None
            try:
                gas_est = fn.estimate_gas({'from': acct_addr})
            except Exception:
                gas_est = 100000
            gas_mult = float(os.getenv('ERC8004_GAS_MULT', '1.5'))
            min_gas = int(os.getenv('ERC8004_MIN_GAS', '200000'))
            gas_limit = max(int(gas_est * gas_mult), min_gas)

            gas_price = self._w3.eth.gas_price
            gas_price_mult = float(os.getenv('ERC8004_GAS_PRICE_MULT', '1.2'))
            try:
                override_gwei = os.getenv('ERC8004_GAS_PRICE_MULT')
                if override_gwei:
                    gas_price = self._w3.to_wei(float(override_gwei), 'gwei')
                else:
                    gas_price = int(gas_price * gas_price_mult)
            except Exception:
                pass

            tx = fn.build_transaction(
                {
                    'from': acct_addr,
                    'nonce': self._w3.eth.get_transaction_count(acct_addr, 'pending'),
                    'gas': gas_limit,
                    'gasPrice': gas_price,
                }
            )
            logger.info('ERC-8004: authorize gas_limit=%s gas_price=%s wei (est=%s)', gas_limit, gas_price, gas_est)
            signed = self._w3.eth.account.sign_transaction(tx, pk)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            try:
                receipt = self._w3.eth.wait_for_transaction_receipt(
                    tx_hash, timeout=self._tx_timeout_sec
                )
            except TimeExhausted:
                receipt = self._w3.eth.get_transaction_receipt(tx_hash)
            logger.info('ERC-8004: authorize feedback tx mined: %s (status=%s)', tx_hash.hex(), getattr(receipt, 'status', None))
            if getattr(receipt, 'status', 0) != 1:
                self._log_tx_failure_details(tx_hash, receipt)
                return None
            # Prefer contract views to confirm and obtain FeedbackAuthID
            feedback_auth_id = None
            matched_event = None
            try:
                contract = self._get_reputation_contract()
                logger.info('ERC-8004: contract: %s', contract)
                logger.info('ERC-8004: has function: %s', hasattr(contract.functions, 'isFeedbackAuthorized') if contract else None)
                # Block and poll until authorized or timeout
                import time as _time
                total_wait_sec = float(os.getenv('ERC8004_VIEW_WAIT_SEC', '8'))
                poll_ms = float(os.getenv('ERC8004_VIEW_POLL_MS', '300'))
                deadline = _time.time() + total_wait_sec
                while feedback_auth_id is None and _time.time() < deadline and contract is not None:
                    try:
                        if hasattr(contract.functions, 'isFeedbackAuthorized'):
                            logger.info('ERC-8004: isFeedbackAuthorized(client=%s, server=%s)', client_agent_id_int, server_agent_id_int)
                            is_auth, fid = contract.functions.isFeedbackAuthorized(client_agent_id_int, server_agent_id_int).call()
                            logger.info('ERC-8004: isFeedbackAuthorized(client=%s, server=%s) -> %s, %s', client_agent_id_int, server_agent_id_int, is_auth, fid)
                            if is_auth and fid is not None:
                                if hasattr(fid, 'hex'):
                                    h = fid.hex()
                                    feedback_auth_id = h if h.startswith('0x') else f'0x{h}'
                                else:
                                    feedback_auth_id = str(fid)
                                matched_event = 'isFeedbackAuthorized(view)'
                                break
                        if hasattr(contract.functions, 'getFeedbackAuthId'):
                            fid2 = contract.functions.getFeedbackAuthId(client_agent_id_int, server_agent_id_int).call()
                            logger.info('ERC-8004: getFeedbackAuthId(client=%s, server=%s) -> %s', client_agent_id_int, server_agent_id_int, fid2)
                            if fid2 is not None:
                                if hasattr(fid2, 'hex'):
                                    h2 = fid2.hex()
                                    feedback_auth_id = h2 if h2.startswith('0x') else f'0x{h2}'
                                else:
                                    feedback_auth_id = str(fid2)
                                matched_event = 'getFeedbackAuthId(view)'
                                break
                    except Exception as _e:
                        logger.debug('ERC-8004: view poll error: %s', _e)
                    _time.sleep(max(0.05, poll_ms / 1000.0))
            except Exception as e:
                logger.debug('ERC-8004: view resolution for FeedbackAuthID failed: %s', e)

            result: dict[str, Any] = {
                'tx_hash': tx_hash.hex(),
                'client_agent_id': client_agent_id_int,
                'server_agent_id': server_agent_id_int,
            }
            # Helpful: include client address if we can resolve it by id
            try:
                identity = self._get_identity_contract()
                if identity is not None:
                    # Attempt resolveById if available; otherwise ignore
                    if hasattr(identity.functions, 'resolveById'):
                        info = identity.functions.resolveById(client_agent_id_int).call()
                        # Expected tuple: (id, domain, address)
                        if info and len(info) >= 3:
                            result['client_address'] = info[2]
            except Exception:
                pass
            if feedback_auth_id:
                logger.info('ERC-8004: FeedbackAuthID resolved via %s: %s', matched_event, feedback_auth_id)
            else:
                # Do not invent an id; leave None so a subsequent read can pick it up
                logger.info('ERC-8004: FeedbackAuthID not available immediately; will rely on subsequent view lookups.')
            if feedback_auth_id:
                result['feedback_auth_id'] = str(feedback_auth_id)
            return result
        except Exception as e:
            logger.warning('ERC-8004: authorize_feedback failed: %s', e)
            return None

    def get_agent_by_domain(self, domain: str) -> Optional[dict[str, Any]]:
        """Resolve agent info by domain.

        Tries a direct resolveByDomain/domain-based function on the registry.
        If unavailable, falls back to resolving by address inferred from
        variant-specific private keys (finder/reserve) based on the domain name.
        """

        logger.info('************** ERC-8004: get_agent_by_domain: %s', domain)
        if not (self._w3 and self.identity_registry):
            logger.info('************** ERC-8004: not self._w3: %s', self._w3)
            logger.info('************** ERC-8004: not self.identity_registry: %s', self.identity_registry)
            return None
        identity = self._get_identity_contract()
        if identity is None:
            logger.info('************** ERC-8004: no identity contract: %s', domain)
            return None
        # Attempt direct domain resolver methods
        logger.info('************** ERC-8004: attempt direct domain resolver methods: %s', identity)
        for fn_name in ('resolveByDomain', 'getAgentByDomain', 'resolveDomain'):
            try:
                fn = getattr(identity.functions, fn_name)
                result = fn(domain).call()
                # Expect (agentId, agentDomain, agentAddress) or similar tuple
                if isinstance(result, (list, tuple)) and len(result) >= 3:
                    agent_id = int(result[0]) if result[0] is not None else 0
                    agent_domain = result[1]
                    agent_addr = result[2]
                    if agent_id > 0:
                        return {'agent_id': agent_id, 'domain': agent_domain, 'address': agent_addr}
            except Exception:
                pass

        # Fallback: infer address by variant-specific private key and resolveByAddress
        pk = None
        dom_lower = (domain or '').lower()
        if 'finder' in dom_lower:
            pk = os.getenv('ERC8004_PRIVATE_KEY_FINDER') 
        elif 'reserve' in dom_lower:
            pk = os.getenv('ERC8004_PRIVATE_KEY_RESERVE')
        elif 'assistant' in dom_lower:
            pk = os.getenv('ERC8004_PRIVATE_KEY_ASSISTANT')
        if pk:
            try:
                addr = self._w3.eth.account.from_key(pk).address
                info = identity.functions.resolveByAddress(addr).call()
                if info and len(info) >= 3 and int(info[0]) > 0:
                    return {'agent_id': int(info[0]), 'domain': info[1], 'address': info[2]}
            except Exception:
                return None
        return None

    def check_feedback_authorized(self, client_agent_id: int, server_agent_id: int) -> dict[str, Any]:
        """Check authorization status and current FeedbackAuthID for a client-server pair.

        Returns dict: { 'isAuthorized': bool|None, 'feedbackAuthId': str|None }
        Logs details; read-only, no txs.
        """
        result: dict[str, Any] = {'isAuthorized': None, 'feedbackAuthId': None}

        logger.info('ERC-8004: check_feedback_authorized %s, %s.', client_agent_id, server_agent_id)
        try:
            contract = self._get_reputation_contract()
            if contract is None:
                logger.info('ERC-8004: check_feedback_authorized skipped (no reputation contract).')
                return result
            # Prefer combined view first
            try:
                logger.info('ERC-8004: check isFeedbackAuthorized ')
                if hasattr(contract.functions, 'isFeedbackAuthorized'):
                    logger.info('ERC-8004: isFeedbackAuthorized ')
                    is_auth, fid = contract.functions.isFeedbackAuthorized(int(client_agent_id), int(server_agent_id)).call()
                    logger.info('ERC-8004: isFeedbackAuthorized finished')
                    logger.info('ERC-8004: isFeedbackAuthorized(client=%s, server=%s) -> %s, %s', client_agent_id, server_agent_id, is_auth, fid)
                    result['isAuthorized'] = bool(is_auth)
                    if fid is not None:
                        result['feedbackAuthId'] = fid.hex() if hasattr(fid, 'hex') else str(fid)
                        return result
            except Exception as e:
                logger.info('ERC-8004: isFeedbackAuthorized view failed: %s', e)
            # Fallback to id-only view
            try:
                if hasattr(contract.functions, 'getFeedbackAuthId'):
                    fid2 = contract.functions.getFeedbackAuthId(int(client_agent_id), int(server_agent_id)).call()
                    logger.info('ERC-8004: getFeedbackAuthId(client=%s, server=%s) -> %s', client_agent_id, server_agent_id, fid2)
                    if fid2 is not None:
                        result['feedbackAuthId'] = fid2.hex() if hasattr(fid2, 'hex') else str(fid2)
            except Exception as e:
                logger.info('ERC-8004: getFeedbackAuthId view failed: %s', e)
        except Exception as e:
            logger.info('ERC-8004: check_feedback_authorized failed: %s', e)
        return result

    def get_feedback_auth_id(self, client_agent_id: int, server_agent_id: int) -> Optional[str]:
        """Return only the FeedbackAuthID using the contract view.

        Returns hex string or None.
        """
        try:
            contract = self._get_reputation_contract()
            if contract is None or not hasattr(contract.functions, 'getFeedbackAuthId'):
                return None
            fid = contract.functions.getFeedbackAuthId(int(client_agent_id), int(server_agent_id)).call()
            logger.info('ERC-8004: getFeedbackAuthId(client=%s, server=%s) -> %s', client_agent_id, server_agent_id, fid)
            if fid is None:
                return None
            return fid.hex() if hasattr(fid, 'hex') else str(fid)
        except Exception as e:
            logger.info('ERC-8004: get_feedback_auth_id view failed: %s', e)
            return None



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
    - ERC8004_PRIVATE_KEY
    - ERC8004_IDENTITY_REGISTRY
    - ERC8004_REPUTATION_REGISTRY
    """

    def __init__(self, private_key: Optional[str] = None, rpc_url: Optional[str] = None) -> None:
        self.enabled = os.getenv('ERC8004_ENABLED', 'false').lower() == 'true'
        self.rpc_url = rpc_url or os.getenv('ERC8004_RPC_URL')
        self.private_key = private_key or os.getenv('ERC8004_PRIVATE_KEY')
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
        if self.enabled and self.rpc_url and self.private_key:
            try:
                self._w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 20}))
                acct = self._w3.eth.account.from_key(self.private_key)
                logger.info('ERC-8004 Web3 client initialized (address=%s)', acct.address)
            except Exception as e:
                logger.warning('ERC-8004: failed to init Web3 client: %s', e)
                self._w3 = None

        # Lazy-loaded contracts
        self._identity_contract = None
        # Attempt to hydrate contract addresses from deployment.json if env not set
        if self.enabled and not self.identity_registry:
            self._load_contract_addresses_from_deployment()

    def is_enabled(self) -> bool:
        return self.enabled

    def ensure_identity(self, agent_name: str, agent_domain: Optional[str] = None) -> None:
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
                acct_addr = self._w3.eth.account.from_key(self.private_key).address
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
                gas_est = None
                try:
                    gas_est = fn.estimate_gas({'from': acct_addr, 'value': value})
                except Exception:
                    gas_est = 300000
                tx = fn.build_transaction(
                    {
                        'from': acct_addr,
                        'nonce': self._w3.eth.get_transaction_count(acct_addr),
                        'gas': int(gas_est * 1.2),
                        'gasPrice': self._w3.eth.gas_price,
                        'value': value,
                    }
                )
                signed = self._w3.eth.account.sign_transaction(tx, self.private_key)
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
            acct_addr = self._w3.eth.account.from_key(self.private_key).address
            tx = contract.functions.register(agent_name).build_transaction(
                {
                    'from': acct_addr,
                    'nonce': self._w3.eth.get_transaction_count(acct_addr),
                    'gas': 300000,
                }
            )
            signed = self._w3.eth.account.sign_transaction(tx, self.private_key)
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

    def record_reservation(self, payload: dict[str, Any]) -> None:
        """Record a reservation action for reputation/audit.

        payload keys suggested: booking_id, listing_url, check_in, check_out, guests
        This default implementation only logs. Replace with contract calls as needed.
        """
        if not self.enabled:
            return
        logger.info(
            'ERC-8004: record_reservation called payload=%s (reputation_registry=%s)',
            payload,
            self.reputation_registry,
        )



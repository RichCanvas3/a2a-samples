# pylint: disable=logging-fstring-interpolation
import asyncio
import json
import os
import uuid

from typing import Any, AsyncIterator, Dict, List

import httpx
from urllib.parse import urlparse, urlunparse

from a2a.client import A2ACardResolver
from a2a.types import (
    AgentCard,
    MessageSendParams,
    SendMessageRequest,
    SendMessageResponse,
    SendMessageSuccessResponse,
    Task,
)
from dotenv import load_dotenv
from openai import OpenAI
from remote_agent_connection import (
    RemoteAgentConnections,
    TaskUpdateCallback,
)
from common_utils.erc8004_adapter import Erc8004Adapter


load_dotenv()


SYSTEM_PROMPT = (
    "You are an expert Routing Delegator. Your job is to route user requests "
    "to remote agents using the send_message tool, and then present the remote "
    "agent's result to the user. Rely on tools only; do not fabricate results. "
    "If the user wants to submit feedback after a reservation, use the leave_feedback tool."
)


def create_send_message_payload(
    text: str, task_id: str | None = None, context_id: str | None = None
) -> dict[str, Any]:
    """Helper function to create the payload for sending a task."""
    payload: dict[str, Any] = {
        'message': {
            'role': 'user',
            'parts': [{'type': 'text', 'text': text}],
            'messageId': uuid.uuid4().hex,
        },
    }

    if task_id:
        payload['message']['taskId'] = task_id

    if context_id:
        payload['message']['contextId'] = context_id
    return payload


class RoutingAgent:
    """The Routing agent.

    This is the agent responsible for choosing which remote seller agents to send
    tasks to and coordinate their work.
    """

    def __init__(
        self,
        task_callback: TaskUpdateCallback | None = None,
    ):
        self.task_callback = task_callback
        self.remote_agent_connections: dict[str, RemoteAgentConnections] = {}
        self.cards: dict[str, AgentCard] = {}
        self.agents: str = ''
        self.remote_agent_addresses: list[str] = []
        self.feedback_records: list[dict[str, Any]] = []
        self.authorized_feedback_agent_ids: set[int] = set()
        self.authorized_feedback_agent_addr_by_id: dict[int, str] = {}
        self.authorized_feedback_auth_id_by_target_id: dict[int, str] = {}

    async def _async_init_components(
        self, remote_agent_addresses: list[str]
    ) -> None:
        """Asynchronous part of initialization."""
        # Persist the configured addresses for lazy refresh
        self.remote_agent_addresses = list(remote_agent_addresses)
        # Use a single httpx.AsyncClient for all card resolutions for efficiency
        async with httpx.AsyncClient(timeout=30) as client:
            for address in remote_agent_addresses:
                card_resolver = A2ACardResolver(
                    client, address
                )  # Constructor is sync
                try:
                    card = (
                        await card_resolver.get_agent_card()
                    )  # get_agent_card is async

                    # Prefer to show the original address to the UI, but keep the
                    # actual connection URL as the resolved/fetchable address.
                    try:
                        card.url = address
                    except Exception:
                        pass

                    remote_connection = RemoteAgentConnections(
                        agent_card=card, agent_url=address
                    )
                    self.remote_agent_connections[card.name] = remote_connection
                    self.cards[card.name] = card
                except httpx.ConnectError as e:
                    # Retry strategy for .localhost subdomains and missing variant path
                    try:
                        parsed = urlparse(address if '://' in address else f'http://{address}')
                        hostname = parsed.hostname or ''
                        port = parsed.port
                        scheme = parsed.scheme or 'http'
                        path = parsed.path or '/'

                        new_host = hostname
                        new_path = path
                        # If subdomain like finder.localhost, use localhost for fetching
                        if hostname.endswith('.localhost'):
                            new_host = 'localhost'
                        # Ensure path contains variant when implied by hostname
                        if (('finder' in hostname or 'finder' in path) and not path.startswith('/finder')):
                            new_path = '/finder'
                        if (('reserve' in hostname or 'reserve' in path) and not path.startswith('/reserve')):
                            new_path = '/reserve'

                        if new_host != hostname or new_path != path:
                            netloc = f"{new_host}:{port}" if port else new_host
                            fallback_address = urlunparse((scheme, netloc, new_path, '', '', ''))
                            # Try fetch with fallback
                            card = await A2ACardResolver(client, fallback_address).get_agent_card()
                            try:
                                # Present original nice address to UI if possible
                                card.url = address
                            except Exception:
                                pass
                            remote_connection = RemoteAgentConnections(
                                agent_card=card, agent_url=fallback_address
                            )
                            self.remote_agent_connections[card.name] = remote_connection
                            self.cards[card.name] = card
                            continue
                        # If no rewrite possible, surface original error
                        print(
                            f'ERROR: Failed to get agent card from {address}: {e}'
                        )
                    except Exception as e2:
                        print(
                            f'ERROR: Failed to initialize connection for {address} with fallback: {e2}'
                        )
                except Exception as e:  # Catch other potential errors
                    # Attempt same fallback rewrite strategy for generic errors
                    try:
                        parsed = urlparse(address if '://' in address else f'http://{address}')
                        hostname = parsed.hostname or ''
                        port = parsed.port
                        scheme = parsed.scheme or 'http'
                        path = parsed.path or '/'

                        new_host = hostname
                        new_path = path
                        if hostname.endswith('.localhost'):
                            new_host = 'localhost'
                        if (('finder' in hostname or 'finder' in path) and not path.startswith('/finder')):
                            new_path = '/finder'
                        if (('reserve' in hostname or 'reserve' in path) and not path.startswith('/reserve')):
                            new_path = '/reserve'

                        if new_host != hostname or new_path != path:
                            netloc = f"{new_host}:{port}" if port else new_host
                            fallback_address = urlunparse((scheme, netloc, new_path, '', '', ''))
                            card = await A2ACardResolver(client, fallback_address).get_agent_card()
                            try:
                                card.url = address
                            except Exception:
                                pass
                            remote_connection = RemoteAgentConnections(
                                agent_card=card, agent_url=fallback_address
                            )
                            self.remote_agent_connections[card.name] = remote_connection
                            self.cards[card.name] = card
                            continue
                        print(
                            f'ERROR: Failed to initialize connection for {address}: {e}'
                        )
                    except Exception as e2:
                        print(
                            f'ERROR: Failed to initialize connection for {address} with fallback: {e2}'
                    )

        # Populate self.agents using the logic from original __init__ (via list_remote_agents)
        agent_info = []
        for agent_detail_dict in self.list_remote_agents():
            agent_info.append(json.dumps(agent_detail_dict))
        self.agents = '\n'.join(agent_info)

    async def _refresh_cards_if_needed(self) -> None:
        """Attempt to refresh remote agent cards if none are loaded."""
        if self.remote_agent_connections:
            return
        if not self.remote_agent_addresses:
            return
        async with httpx.AsyncClient(timeout=30) as client:
            for address in self.remote_agent_addresses:
                try:
                    card = await A2ACardResolver(client, address).get_agent_card()
                    try:
                        card.url = address
                    except Exception:
                        pass
                    self.remote_agent_connections[card.name] = RemoteAgentConnections(
                        agent_card=card, agent_url=address
                    )
                    self.cards[card.name] = card
                    continue
                except Exception:
                    pass
                # Retry with .localhost rewrite if applicable
                try:
                    parsed = urlparse(address if '://' in address else f'http://{address}')
                    hostname = parsed.hostname or ''
                    port = parsed.port
                    scheme = parsed.scheme or 'http'
                    path = parsed.path or '/'
                    new_host = 'localhost' if hostname.endswith('.localhost') else hostname
                    new_path = path
                    if (('finder' in hostname or 'finder' in path) and not path.startswith('/finder')):
                        new_path = '/finder'
                    if (('reserve' in hostname or 'reserve' in path) and not path.startswith('/reserve')):
                        new_path = '/reserve'
                    if new_host != hostname or new_path != path:
                        netloc = f"{new_host}:{port}" if port else new_host
                        fallback_address = urlunparse((scheme, netloc, new_path, '', '', ''))
                        card = await A2ACardResolver(client, fallback_address).get_agent_card()
                        try:
                            card.url = address
                        except Exception:
                            pass
                        self.remote_agent_connections[card.name] = RemoteAgentConnections(
                            agent_card=card, agent_url=fallback_address
                        )
                        self.cards[card.name] = card
                except Exception:
                    continue
        # Rebuild agents string
        agent_info = []
        for agent_detail_dict in self.list_remote_agents():
            agent_info.append(json.dumps(agent_detail_dict))
        self.agents = '\n'.join(agent_info)

    @classmethod
    async def create(
        cls,
        remote_agent_addresses: list[str],
        task_callback: TaskUpdateCallback | None = None,
    ) -> 'RoutingAgent':
        """Create and asynchronously initialize an instance of the RoutingAgent."""
        instance = cls(task_callback)
        await instance._async_init_components(remote_agent_addresses)
        return instance

    def _openai_client(self) -> OpenAI:
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError('OPENAI_API_KEY environment variable is not set')
        return OpenAI(api_key=api_key)

    def _build_system_prompt(self, state: Dict[str, Any]) -> str:
        current_active = self._get_active_agent_name(state)
        # Present exact agent names to minimize LLM drift
        available_names = list(self.remote_agent_connections.keys())
        # Build a fresh summary of available agents from current cards
        available_agents_summary = [
            {'name': c.name, 'description': c.description}
            for c in self.cards.values()
        ]
        return (
            f"{SYSTEM_PROMPT}\n\n"
            f"Available Agents: {available_agents_summary}\n"
            f"Agent Names (use exactly one of these in agent_name): {available_names}\n"
            f"Currently Active Seller Agent: {current_active}"
        )

    def _get_active_agent_name(self, state: Dict[str, Any]) -> str:
        if (
            'session_id' in state
            and state.get('session_active')
            and 'active_agent' in state
        ):
            return f"{state['active_agent']}"
        return 'None'

    def ensure_session_state(self, state: Dict[str, Any]) -> None:
        if not state.get('session_active'):
            state['session_id'] = state.get('session_id') or str(uuid.uuid4())
            state['session_active'] = True

    def _resolve_agent_name(self, requested: str) -> str | None:
        """Resolve a requested agent name to a known remote agent name.

        Tries exact match, case-insensitive match, simple aliases.
        """
        if not requested:
            return None
        names = list(self.remote_agent_connections.keys())
        # Exact
        if requested in self.remote_agent_connections:
            return requested
        # Case-insensitive exact
        for n in names:
            if n.lower() == requested.lower():
                return n
        # Simple aliases
        alias_map = {
            'seller agent': 'Airbnb Agent - Finder',
            'finder': 'Airbnb Agent - Finder',
            'search agent': 'Airbnb Agent - Finder',
            'reserve': 'Airbnb Agent - Reserve',
            'reservation agent': 'Airbnb Agent - Reserve',
            'booking agent': 'Airbnb Agent - Reserve',
            'weather': 'Weather Agent',
        }
        normalized = requested.lower().strip()
        if normalized in alias_map and alias_map[normalized] in self.remote_agent_connections:
            return alias_map[normalized]
        # Substring heuristic
        for n in names:
            if normalized in n.lower() or n.lower() in normalized:
                return n
        return None

    def list_remote_agents(self):
        """List the available remote agents you can use to delegate the task."""
        if not self.cards:
            return []

        remote_agent_info = []
        for card in self.cards.values():
            print(f'Found agent card: {card.model_dump(exclude_none=True)}')
            print('=' * 100)
            remote_agent_info.append(
                {'name': card.name, 'description': card.description}
            )
        return remote_agent_info

    async def _authorize_feedback(self, client_agent_name: str, target_agent_name: str) -> dict[str, Any]:
        """Call acceptFeedback(client_agent_id, target_agent_id) in reputation registry.

        Stores the authorized client's address in memory for FeedbackAuthID in exports.
        """
        # Resolve names to registry ids
        target_resolved = self._resolve_agent_name(target_agent_name) or ''
        is_reserve = 'reserve' in target_resolved.lower()
        target_domain_env = 'RESERVE_DOMAIN' if is_reserve else 'FINDER_DOMAIN'
        target_domain = os.getenv(target_domain_env, 'reserve.localhost:10002' if is_reserve else 'finder.localhost:10002')

        adapter = Erc8004Adapter()
        target_info = adapter.get_agent_by_domain(target_domain)
        if not target_info or not target_info.get('agent_id'):
            return {'status': 'error', 'message': f'Could not resolve target agent {target_agent_name}'}
        target_id = int(target_info['agent_id'])

        # Client (assistant) id: assume assistant domain is configured
        client_domain = os.getenv('ERC8004_AGENT_DOMAIN_ASSISTANT') or os.getenv('ERC8004_AGENT_DOMAIN') or 'assistant.localhost:8083'
        client_info = adapter.get_agent_by_domain(client_domain)
        if not client_info or not client_info.get('agent_id'):
            return {'status': 'error', 'message': f'Could not resolve client agent {client_agent_name}'}
        client_id = int(client_info['agent_id'])

        # Execute authorization tx signed by the SERVER (Finder/Reserve) key via adapter
        # No user-involved steps; adapter handles acceptFeedback and returns FeedbackAuthID
        # Sign with the SERVER agent's key (Finder/Reserve), not the assistant's key
        server_pk = (
            os.getenv('ERC8004_PRIVATE_KEY_RESERVE') if is_reserve else os.getenv('ERC8004_PRIVATE_KEY_FINDER')
        ) or os.getenv('ERC8004_PRIVATE_KEY')
        auth_result = adapter.authorize_feedback_from_client(
            client_agent_id=client_id,
            server_agent_id=target_id,
            signing_private_key=server_pk,
        )
        if not auth_result:
            return {'status': 'error', 'message': 'Authorization transaction failed.'}

        # Save mapping for FeedbackAuthID (store target id -> assistant address)
        try:
            self.authorized_feedback_agent_ids.add(target_id)
            client_addr = auth_result.get('client_address') or client_info.get('address', '')
            if not client_addr:
                # Derive from assistant signing key if available
                pk_env = os.getenv('ERC8004_PRIVATE_KEY_ASSISTANT')
                if pk_env:
                    try:
                        from eth_account import Account  # type: ignore
                        client_addr = Account.from_key(pk_env).address
                    except Exception:
                        client_addr = ''
            if client_addr:
                self.authorized_feedback_agent_addr_by_id[target_id] = client_addr
            # Persist event-provided FeedbackAuthID if present
            if auth_result.get('feedback_auth_id'):
                self.authorized_feedback_auth_id_by_target_id[target_id] = str(auth_result['feedback_auth_id'])
        except Exception:
            pass

        result = {
            'status': 'ok',
            'clientAgentId': client_id,
            'targetAgentId': target_id,
            'txHash': auth_result.get('tx_hash'),
        }
        if auth_result.get('feedback_auth_id'):
            result['FeedbackAuthID'] = auth_result['feedback_auth_id']
        return result
    async def submit_feedback(self, agent_name: str, rating: int, comment: str, state: Dict[str, Any]) -> dict[str, Any]:
        """Submit feedback for an agent via ERC-8004 ReputationRegistry.

        agent_name should match a known agent (Finder/Reserve). Rating 1-5.
        """
        # Determine variant and resolve domain
        resolved_name = self._resolve_agent_name(agent_name) or ''
        is_reserve = 'reserve' in resolved_name.lower()
        domain_env = 'RESERVE_DOMAIN' if is_reserve else 'FINDER_DOMAIN'
        fallback_domain = 'reserve.localhost:10002' if is_reserve else 'finder.localhost:10002'
        domain = os.getenv(domain_env, fallback_domain)

        adapter = Erc8004Adapter()
        info = adapter.get_agent_by_domain(domain)
        if not info or not info.get('agent_id'):
            return {
                'status': 'error',
                'message': f'Could not resolve agent by domain {domain} for feedback.'
            }
        try:
            agent_id = int(info['agent_id'])
        except Exception:
            return {'status': 'error', 'message': 'Invalid agent id from registry.'}

        # Clamp rating to 1..5
        try:
            rating_int = max(1, min(5, int(rating)))
        except Exception:
            rating_int = 5

        # Ensure we have authorization and a FeedbackAuthID
        feedback_auth_id: str | None = None
        if agent_id in self.authorized_feedback_auth_id_by_target_id:
            feedback_auth_id = self.authorized_feedback_auth_id_by_target_id.get(agent_id)
        else:
            try:
                # Resolve assistant client id
                client_domain = (
                    os.getenv('ERC8004_AGENT_DOMAIN_ASSISTANT')
                    or os.getenv('ERC8004_AGENT_DOMAIN')
                    or 'assistant.localhost:8083'
                )
                client_info = adapter.get_agent_by_domain(client_domain)
                if client_info and client_info.get('agent_id'):
                    # Call acceptFeedback with server=target_id (reserve/finder), client=assistant
                    # Server key (Finder/Reserve)
                    server_pk2 = (
                        os.getenv('ERC8004_PRIVATE_KEY_RESERVE') if is_reserve else os.getenv('ERC8004_PRIVATE_KEY_FINDER')
                    )
                    auth_res = adapter.authorize_feedback_from_client(
                        client_agent_id=int(client_info['agent_id']),
                        server_agent_id=agent_id,
                        signing_private_key=server_pk2,
                    )
                    if auth_res and auth_res.get('feedback_auth_id'):
                        feedback_auth_id = str(auth_res['feedback_auth_id'])
                        self.authorized_feedback_auth_id_by_target_id[agent_id] = feedback_auth_id
                        # Also keep client address for fallback display
                        client_addr = auth_res.get('client_address') or client_info.get('address', '')
                        if not client_addr and os.getenv('ERC8004_PRIVATE_KEY_ASSISTANT'):
                            try:
                                from eth_account import Account  # type: ignore
                                client_addr = Account.from_key(os.getenv('ERC8004_PRIVATE_KEY_ASSISTANT')).address
                            except Exception:
                                client_addr = ''
                        if client_addr:
                            self.authorized_feedback_agent_addr_by_id[agent_id] = client_addr
                        self.authorized_feedback_agent_ids.add(agent_id)
            except Exception:
                pass

        # Do NOT write feedback on-chain. Only pre-authorization is on-chain.
        # Feedback is stored client-side and exposed via feedback.json.
        # Build feedback record for export endpoint
        try:
            chain_id = os.getenv('ERC8004_CHAIN_ID', '11155111')
            # Prefer event-provided FeedbackAuthID; fallback to CAIP-10 with client address if available
            if not feedback_auth_id and agent_id in self.authorized_feedback_agent_ids:
                auth_addr = self.authorized_feedback_agent_addr_by_id.get(agent_id, '')
                if auth_addr:
                    feedback_auth_id = f'eip155:{chain_id}:{auth_addr}'
            # Pull task/context ids if available
            task_ids_by_agent = state.get('task_ids_by_agent', {}) if isinstance(state.get('task_ids_by_agent'), dict) else {}
            context_ids_by_agent = state.get('context_ids_by_agent', {}) if isinstance(state.get('context_ids_by_agent'), dict) else {}
            task_id = task_ids_by_agent.get(agent_name) or task_ids_by_agent.get(resolved_name) or ''
            context_id = context_ids_by_agent.get(agent_name) or context_ids_by_agent.get(resolved_name) or ''
            agent_skill_id = ('reserve:v1' if is_reserve else 'finder:v1')
            rating_pct = int(max(0, min(100, rating_int * 20)))
            record: Dict[str, Any] = {
                'FeedbackAuthID': feedback_auth_id,
                'AgentSkillId': agent_skill_id,
                'TaskId': task_id,
                'contextId': context_id,
                'Rating': rating_pct,
                'Domain': domain,
                'Data': {'notes': str(comment)},
            }
            # Optionally include ProofOfPayment if caller provided one in state/env
            proof_tx = state.get('payment_tx_hash') if isinstance(state, dict) else None
            proof_tx = proof_tx or os.getenv('FEEDBACK_PAYMENT_TX')
            if proof_tx:
                record['ProofOfPayment'] = {'txHash': str(proof_tx)}
            self.feedback_records.append(record)
        except Exception:
            pass
        return {
            'status': 'ok',
            'agentId': agent_id,
            'domain': domain,
            'rating': rating_int,
            'comment': str(comment),
        }

    async def send_message(self, agent_name: str, task: str, state: Dict[str, Any]):
        """Sends a task to remote seller agent.

        This will send a message to the remote agent named agent_name.

        Args:
            agent_name: The name of the agent to send the task to.
            task: The comprehensive conversation context summary
                and goal to be achieved regarding user inquiry and purchase request.
            tool_context: The tool context this method runs in.

        Yields:
            A dictionary of JSON data.
        """
        resolved_name = self._resolve_agent_name(agent_name)
        if not resolved_name:
            raise ValueError(f'Agent {agent_name} not found')
        state['active_agent'] = resolved_name
        client = self.remote_agent_connections[resolved_name]

        if not client:
            raise ValueError(f'Client not available for {agent_name}')
        # Track task ids per remote agent so we don't send a task id from one
        # agent to another.
        task_ids_by_agent = state.get('task_ids_by_agent')
        if task_ids_by_agent is None or not isinstance(task_ids_by_agent, dict):
            task_ids_by_agent = {}
            state['task_ids_by_agent'] = task_ids_by_agent
        # Only include a task id if we already have one for this agent.
        task_id = task_ids_by_agent.get(agent_name)

        # Track context ids per remote agent as well.
        context_ids_by_agent = state.get('context_ids_by_agent')
        if context_ids_by_agent is None or not isinstance(context_ids_by_agent, dict):
            context_ids_by_agent = {}
            state['context_ids_by_agent'] = context_ids_by_agent
        if agent_name in context_ids_by_agent:
            context_id = context_ids_by_agent[agent_name]
        else:
            context_id = str(uuid.uuid4())
            context_ids_by_agent[agent_name] = context_id

        message_id = ''
        metadata = {}
        if 'input_message_metadata' in state:
            metadata.update(**state['input_message_metadata'])
            if 'message_id' in state['input_message_metadata']:
                message_id = state['input_message_metadata']['message_id']
        if not message_id:
            message_id = str(uuid.uuid4())

        payload = {
            'message': {
                'role': 'user',
                'parts': [
                    {'type': 'text', 'text': task}
                ],  # Use the 'task' argument here
                'messageId': message_id,
            },
        }

        # Attach client agent id (assistant) for downstream server-side authorization
        try:
            adapter = Erc8004Adapter()
            client_domain = (
                os.getenv('ERC8004_AGENT_DOMAIN_ASSISTANT')
                or os.getenv('ERC8004_AGENT_DOMAIN')
                or 'assistant.localhost:8083'
            )
            client_info = adapter.get_agent_by_domain(client_domain)
            client_id_val = int(client_info['agent_id']) if (client_info and client_info.get('agent_id')) else None
            if client_id_val is not None:
                payload['message']['metadata'] = {'client_agent_id': str(client_id_val)}
        except Exception:
            pass

        if task_id:
            payload['message']['taskId'] = task_id

        if context_id:
            payload['message']['contextId'] = context_id

        message_request = SendMessageRequest(
            id=message_id, params=MessageSendParams.model_validate(payload)
        )
        send_response: SendMessageResponse = await client.send_message(
            message_request=message_request
        )
        print(
            'send_response',
            send_response.model_dump_json(exclude_none=True, indent=2),
        )

        if not isinstance(send_response.root, SendMessageSuccessResponse):
            print('received non-success response. Aborting get task ')
            return None

        if not isinstance(send_response.root.result, Task):
            print('received non-task response. Aborting get task ')
            return None

        # Persist the real task id for this agent for subsequent updates/messages
        try:
            task_ids_by_agent[agent_name] = send_response.root.result.id  # type: ignore[attr-defined]
        except Exception:
            pass

        return send_response.root.result

    async def handle(self, user_text: str, state: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        """Core routing loop using OpenAI tool-calling.

        Yields structured dict events: {type: 'tool_call'|'tool_response'|'final', name?, content}
        """
        self.ensure_session_state(state)
        await self._refresh_cards_if_needed()

        client = self._openai_client()
        model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')

        allowed_agents = list(self.remote_agent_connections.keys())
        tools: List[Dict[str, Any]] = [
            {
                'type': 'function',
                'function': {
                    'name': 'send_message',
                    'description': 'Send a task to a remote agent and obtain its response.',
                    'parameters': {
                        'type': 'object',
                        'properties': {
                            'agent_name': {
                                'type': 'string',
                                'description': 'Name of the remote agent',
                                'enum': allowed_agents,
                            },
                            'task': {'type': 'string', 'description': 'Task to send to the agent'},
                        },
                        'required': ['agent_name', 'task'],
                    },
                },
            },
            {
                'type': 'function',
                'function': {
                    'name': 'leave_feedback',
                    'description': 'Leave feedback for an agent via ERC-8004 reputation registry (rating 1-5).',
                    'parameters': {
                        'type': 'object',
                        'properties': {
                            'agent_name': {
                                'type': 'string',
                                'description': 'Name of the agent to leave feedback for',
                                'enum': allowed_agents,
                            },
                            'rating': {'type': 'integer', 'minimum': 1, 'maximum': 5},
                            'comment': {'type': 'string'},
                        },
                        'required': ['agent_name', 'rating', 'comment'],
                    },
                },
            },
            {
                'type': 'function',
                'function': {
                    'name': 'authorize_feedback',
                    'description': 'Authorize a client (assistant) agent to provide feedback for a target agent.',
                    'parameters': {
                        'type': 'object',
                        'properties': {
                            'client_agent_name': {
                                'type': 'string',
                                'description': 'Name of the client agent (Assistant) providing feedback',
                                'enum': allowed_agents + ['assistant'],
                            },
                            'target_agent_name': {
                                'type': 'string',
                                'description': 'Name of the target agent (Finder/Reserve) to authorize feedback for',
                                'enum': allowed_agents,
                            },
                        },
                        'required': ['client_agent_name', 'target_agent_name'],
                    },
                },
            },
        ]

        system_prompt = self._build_system_prompt(state)
        messages: List[Dict[str, Any]] = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_text},
        ]

        final_text = ''
        max_steps = 6
        for _ in range(max_steps):
            step_resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice='auto',
            )

            assistant_msg = step_resp.choices[0].message
            tool_calls = getattr(assistant_msg, 'tool_calls', None) or []

            if not tool_calls:
                final_text = assistant_msg.content or ''
                break

            messages.append({
                'role': 'assistant',
                'content': assistant_msg.content or '',
                'tool_calls': [tc.to_dict() for tc in tool_calls],
            })

            for tc in tool_calls:
                func_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or '{}')
                except json.JSONDecodeError:
                    args = {}

                yield {'type': 'tool_call', 'name': func_name, 'content': args}

                if func_name == 'send_message':
                    agent_name = args.get('agent_name')
                    task = args.get('task', '')
                    try:
                        result = await self.send_message(agent_name, task, state)
                        result_json = (
                            result.model_dump(exclude_none=True) if hasattr(result, 'model_dump') else str(result)
                        )
                        messages.append({
                            'role': 'tool',
                            'tool_call_id': tc.id,
                            'name': func_name,
                            'content': json.dumps(result_json),
                        })
                        yield {'type': 'tool_response', 'name': func_name, 'content': result_json}
                    except Exception as e:
                        error_msg = f"Routing error: {type(e).__name__}: {e} | Allowed agents: {allowed_agents}"
                        messages.append({
                            'role': 'tool',
                            'tool_call_id': tc.id,
                            'name': func_name,
                            'content': error_msg,
                        })
                        yield {'type': 'tool_response', 'name': func_name, 'content': {'error': error_msg}}
                elif func_name == 'leave_feedback':
                    agent_name = args.get('agent_name')
                    rating = args.get('rating', 5)
                    comment = args.get('comment', '')
                    try:
                        result = await self.submit_feedback(agent_name, rating, comment, state)
                        messages.append({
                            'role': 'tool',
                            'tool_call_id': tc.id,
                            'name': func_name,
                            'content': json.dumps(result),
                        })
                        yield {'type': 'tool_response', 'name': func_name, 'content': result}
                    except Exception as e:
                        error_msg = f"Feedback error: {type(e).__name__}: {e}"
                        messages.append({
                            'role': 'tool',
                            'tool_call_id': tc.id,
                            'name': func_name,
                            'content': error_msg,
                        })
                        yield {'type': 'tool_response', 'name': func_name, 'content': {'error': error_msg}}
                elif func_name == 'authorize_feedback':
                    client_agent_name = args.get('client_agent_name')
                    target_agent_name = args.get('target_agent_name')
                    try:
                        result = await self._authorize_feedback(client_agent_name, target_agent_name)
                        messages.append({
                            'role': 'tool',
                            'tool_call_id': tc.id,
                            'name': func_name,
                            'content': json.dumps(result),
                        })
                        yield {'type': 'tool_response', 'name': func_name, 'content': result}
                    except Exception as e:
                        error_msg = f"Authorize feedback error: {type(e).__name__}: {e}"
                        messages.append({
                            'role': 'tool',
                            'tool_call_id': tc.id,
                            'name': func_name,
                            'content': error_msg,
                        })
                        yield {'type': 'tool_response', 'name': func_name, 'content': {'error': error_msg}}
                else:
                    messages.append({
                        'role': 'tool',
                        'tool_call_id': tc.id,
                        'name': func_name,
                        'content': f'Unknown tool: {func_name}',
                    })
                    yield {'type': 'tool_response', 'name': func_name, 'content': {'error': 'Unknown tool'}}

        yield {'type': 'final', 'content': final_text}


def _get_initialized_routing_agent_sync() -> RoutingAgent:
    """Synchronously creates and initializes the RoutingAgent."""

    async def _async_main() -> RoutingAgent:
        # Prefer resolving domains from ERC-8004 Identity Registry, with env fallbacks
        adapter = Erc8004Adapter()

        def _domain_to_url_if_present(info: dict | None) -> str | None:
            # Only build URL if registry returned a domain; no env/hardcoded fallback
            try:
                if info and isinstance(info, dict):
                    dom = (info.get('domain') or '').strip()
                    if dom:
                        if dom.startswith('http://') or dom.startswith('https://'):
                            return dom
                        return f'http://{dom}'
            except Exception:
                return None
            return None

        finder_domain_hint = os.getenv('FINDER_DOMAIN', 'finder.localhost:10002')
        reserve_domain_hint = os.getenv('RESERVE_DOMAIN', 'reserve.localhost:10002')

        finder_info = None
        reserve_info = None
        try:
            finder_info = adapter.get_agent_by_domain(finder_domain_hint)
        except Exception:
            finder_info = None
        try:
            reserve_info = adapter.get_agent_by_domain(reserve_domain_hint)
        except Exception:
            reserve_info = None

        addresses: list[str] = []
        finder_url = _domain_to_url_if_present(finder_info)
        if finder_url:
            addresses.append(finder_url)
        reserve_url = _domain_to_url_if_present(reserve_info)
        if reserve_url:
            addresses.append(reserve_url)
        # Weather remains always available via env or default
        weather_url = os.getenv('WEA_AGENT_URL', 'http://localhost:10001')
        addresses.append(weather_url)

        routing_agent_instance = await RoutingAgent.create(
            remote_agent_addresses=addresses
        )
        return routing_agent_instance

    try:
        return asyncio.run(_async_main())
    except RuntimeError as e:
        if 'asyncio.run() cannot be called from a running event loop' in str(e):
            print(
                f'Warning: Could not initialize RoutingAgent with asyncio.run(): {e}. '
                'This can happen if an event loop is already running (e.g., in Jupyter). '
                'Consider initializing RoutingAgent within an async function in your application.'
            )
        raise


root_agent = _get_initialized_routing_agent_sync()

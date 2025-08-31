# pylint: disable=logging-fstring-interpolation
import asyncio
import json
import os
import uuid

from typing import Any, AsyncIterator, Dict, List

import httpx

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


load_dotenv()


SYSTEM_PROMPT = (
    "You are an expert Routing Delegator. Your job is to route user requests "
    "to remote agents using the send_message tool, and then present the remote "
    "agent's result to the user. Rely on tools only; do not fabricate results."
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

    async def _async_init_components(
        self, remote_agent_addresses: list[str]
    ) -> None:
        """Asynchronous part of initialization."""
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

                    remote_connection = RemoteAgentConnections(
                        agent_card=card, agent_url=address
                    )
                    self.remote_agent_connections[card.name] = remote_connection
                    self.cards[card.name] = card
                except httpx.ConnectError as e:
                    print(
                        f'ERROR: Failed to get agent card from {address}: {e}'
                    )
                except Exception as e:  # Catch other potential errors
                    print(
                        f'ERROR: Failed to initialize connection for {address}: {e}'
                    )

        # Populate self.agents using the logic from original __init__ (via list_remote_agents)
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
        return (
            f"{SYSTEM_PROMPT}\n\n"
            f"Available Agents: {self.agents}\n"
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
        if agent_name not in self.remote_agent_connections:
            raise ValueError(f'Agent {agent_name} not found')
        state['active_agent'] = agent_name
        client = self.remote_agent_connections[agent_name]

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

        client = self._openai_client()
        model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')

        tools: List[Dict[str, Any]] = [
            {
                'type': 'function',
                'function': {
                    'name': 'send_message',
                    'description': 'Send a task to a remote agent and obtain its response.',
                    'parameters': {
                        'type': 'object',
                        'properties': {
                            'agent_name': {'type': 'string', 'description': 'Name of the remote agent'},
                            'task': {'type': 'string', 'description': 'Task to send to the agent'},
                        },
                        'required': ['agent_name', 'task'],
                    },
                },
            }
        ]

        system_prompt = self._build_system_prompt(state)
        messages: List[Dict[str, Any]] = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_text},
        ]

        first = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice='auto',
        )

        assistant_msg = first.choices[0].message
        tool_calls = getattr(assistant_msg, 'tool_calls', None) or []

        if tool_calls:
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
                else:
                    messages.append({
                        'role': 'tool',
                        'tool_call_id': tc.id,
                        'name': func_name,
                        'content': f'Unknown tool: {func_name}',
                    })
                    yield {'type': 'tool_response', 'name': func_name, 'content': {'error': 'Unknown tool'}}

            final = client.chat.completions.create(model=model, messages=messages)
            final_text = final.choices[0].message.content or ''
        else:
            final_text = assistant_msg.content or ''

        yield {'type': 'final', 'content': final_text}


def _get_initialized_routing_agent_sync() -> RoutingAgent:
    """Synchronously creates and initializes the RoutingAgent."""

    async def _async_main() -> RoutingAgent:
        routing_agent_instance = await RoutingAgent.create(
            remote_agent_addresses=[
                os.getenv('AIR_AGENT_URL', 'http://localhost:10002'),
                os.getenv('WEA_AGENT_URL', 'http://localhost:10001'),
            ]
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

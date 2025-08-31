import logging
import json
import os

from typing import Any, Dict, List, Optional

from openai import OpenAI

from a2a.server.agent_execution import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    AgentCard,
    Part,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils.errors import ServerError

from weather_mcp import (
    get_alerts,
    get_forecast,
    get_forecast_by_city,
)


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


SYSTEM_INSTRUCTION = (
    "You are a specialized weather forecast assistant. Your primary function is to "
    "utilize the provided tools to retrieve and relay weather information in response "
    "to user queries. You must rely exclusively on these tools for data and refrain "
    "from inventing information. Ensure that all responses include the detailed output "
    "from the tools used and are formatted in Markdown."
)


class WeatherExecutor(AgentExecutor):
    """An AgentExecutor that uses the OpenAI Chat Completions API with tool calling."""

    def __init__(
        self,
        card: AgentCard,
        *,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self._card = card
        self._active_sessions: set[str] = set()
        self._sessions: dict[str, List[Dict[str, Any]]] = {}

        self._model = model or os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
        self._client = OpenAI(api_key=api_key or os.getenv('OPENAI_API_KEY'))

        # Define tool schemas for function calling
        self._tools: List[Dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": "get_alerts",
                    "description": "Get active weather alerts for a US state (2-letter code).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "state": {"type": "string", "description": "Two-letter US state code, e.g., CA"}
                        },
                        "required": ["state"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_forecast",
                    "description": "Get forecast by latitude and longitude using NWS.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "latitude": {"type": "number"},
                            "longitude": {"type": "number"},
                        },
                        "required": ["latitude", "longitude"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_forecast_by_city",
                    "description": "Get forecast by US city and state (uses geocoding).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string"},
                            "state": {"type": "string"},
                        },
                        "required": ["city", "state"],
                    },
                },
            },
        ]

        self._tool_impl = {
            'get_alerts': get_alerts,
            'get_forecast': get_forecast,
            'get_forecast_by_city': get_forecast_by_city,
        }

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        if not context.current_task:
            await updater.update_status(TaskState.submitted)
        await updater.update_status(TaskState.working)

        session_id = context.context_id
        self._active_sessions.add(session_id)

        try:
            # Build or reuse session message history
            messages = self._sessions.get(session_id)
            if messages is None:
                messages = [
                    {"role": "system", "content": SYSTEM_INSTRUCTION},
                ]
                self._sessions[session_id] = messages

            user_text = self._flatten_parts_to_text(context.message.parts)
            messages.append({"role": "user", "content": user_text})

            # First pass: allow tool calling
            initial = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=self._tools,
                tool_choice="auto",
            )

            assistant_msg = initial.choices[0].message
            tool_calls = getattr(assistant_msg, 'tool_calls', None) or []

            if tool_calls:
                # Notify UI that tools are being used
                await updater.update_status(
                    TaskState.working,
                    message=updater.new_agent_message(
                        [TextPart(text=f"Using tools: {', '.join(tc.function.name for tc in tool_calls)}")]
                    ),
                )

                # Record assistant tool call message
                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_msg.content or "",
                        "tool_calls": [tc.to_dict() for tc in tool_calls],
                    }
                )

                # Execute each tool call and append results
                for tc in tool_calls:
                    func_name = tc.function.name
                    func_args: Dict[str, Any] = {}
                    try:
                        if tc.function.arguments:
                            func_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        func_args = {}

                    impl = self._tool_impl.get(func_name)
                    tool_output = (
                        await impl(**func_args) if impl is not None else f"Unknown tool: {func_name}"
                    )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": func_name,
                            "content": str(tool_output),
                        }
                    )

                # Final pass: produce user-facing response
                final = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                )
                final_text = final.choices[0].message.content or ""
            else:
                final_text = assistant_msg.content or ""

            await updater.add_artifact([TextPart(text=final_text)])
            await updater.update_status(TaskState.completed, final=True)

        finally:
            self._active_sessions.discard(session_id)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        session_id = context.context_id
        if session_id in self._active_sessions:
            logger.info(
                f'Cancellation requested for active weather session: {session_id}'
            )
            self._active_sessions.discard(session_id)
        else:
            logger.debug(
                f'Cancellation requested for inactive weather session: {session_id}'
            )
        raise ServerError(error=UnsupportedOperationError())

    def _flatten_parts_to_text(self, parts: List[Part]) -> str:
        texts: List[str] = []
        for p in parts:
            root = p.root
            if hasattr(root, 'text') and isinstance(root.text, str):
                texts.append(root.text)
            else:
                texts.append('[non-text content omitted]')
        return "\n".join(texts)


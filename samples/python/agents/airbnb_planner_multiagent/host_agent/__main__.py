import asyncio
import os
import sys
import traceback  # Import the traceback module

from collections.abc import AsyncIterator
from pprint import pformat

import gradio as gr

# Allow importing the ERC-8004 adapter from the sibling airbnb_agent package
try:
    from common_utils.erc8004_adapter import Erc8004Adapter  
except Exception:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from common_utils.erc8004_adapter import Erc8004Adapter  # type: ignore

from routing_agent import (
    root_agent as routing_agent,
)


APP_NAME = 'routing_app'
USER_ID = 'default_user'
SESSION_ID = 'default_session'

OPENAI_MODEL = 'gpt-4o-mini'


async def get_response_from_agent(
    message: str,
    history: list[gr.ChatMessage],
) -> AsyncIterator[gr.ChatMessage]:
    """Get response from host agent via OpenAI-routed tool-calling."""
    try:
        state = {}
        async for event in routing_agent.handle(message, state):
            etype = event.get('type')
            if etype == 'tool_call':
                formatted_call = f'```python\n{pformat(event.get("content"), indent=2, width=80)}\n```'
                yield gr.ChatMessage(
                    role='assistant',
                    content=f"🛠️ **Tool Call: {event.get('name')}**\n{formatted_call}",
                )
            elif etype == 'tool_response':
                formatted_response = f'```json\n{pformat(event.get("content"), indent=2, width=80)}\n```'
                yield gr.ChatMessage(
                    role='assistant',
                    content=f"⚡ **Tool Response from {event.get('name')}**\n{formatted_response}",
                )
            elif etype == 'final':
                yield gr.ChatMessage(role='assistant', content=event.get('content', ''))
                break
    except Exception as e:
        print(f'Error in get_response_from_agent (Type: {type(e)}): {e}')
        traceback.print_exc()  # This will print the full traceback
        yield gr.ChatMessage(
            role='assistant',
            content='An error occurred while processing your request. Please check the server logs for details.',
        )


async def main():
    """Main gradio app."""
    # OpenAI routing requires OPENAI_API_KEY in environment.

    # ERC-8004: register Assistant agent identity (optional)
    try:
        assistant_pk = os.getenv('ERC8004_PRIVATE_KEY_ASSISTANT') or os.getenv('ERC8004_PRIVATE_KEY')
        adapter = Erc8004Adapter(private_key=assistant_pk)
        assistant_domain = (
            os.getenv('ERC8004_AGENT_DOMAIN_ASSISTANT')
            or os.getenv('ERC8004_AGENT_DOMAIN')
            or f"assistant.localhost:8083"
        )
        adapter.ensure_identity('assistant', agent_domain=assistant_domain)
    except Exception:
        pass

    with gr.Blocks(
        theme=gr.themes.Ocean(), title='A2A Host Agent with Logo'
    ) as demo:
        gr.Image(
            'https://a2a-protocol.org/latest/assets/a2a-logo-black.svg',
            width=100,
            height=100,
            scale=0,
            show_label=False,
            show_download_button=False,
            container=False,
            show_fullscreen_button=False,
        )
        gr.ChatInterface(
            get_response_from_agent,
            title='A2A Host Agent',
            description='This assistant can help you to check weather and find airbnb accommodation',
        )

    print('Launching Gradio interface...')
    demo.queue().launch(
        server_name='0.0.0.0',
        server_port=8083,
    )
    print('Gradio application has been shut down.')


if __name__ == '__main__':
    asyncio.run(main())

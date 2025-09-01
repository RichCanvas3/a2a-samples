import asyncio
import os
import sys
import logging
import traceback  # Import the traceback module

from collections.abc import AsyncIterator
from pprint import pformat

import gradio as gr
import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

# Ensure the repository package root is on sys.path so we can import sibling packages
_this_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_this_dir)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from common_utils.erc8004_adapter import Erc8004Adapter  # type: ignore

from routing_agent import (
    root_agent as routing_agent,
)


APP_NAME = 'routing_app'
USER_ID = 'default_user'
SESSION_ID = 'default_session'

OPENAI_MODEL = 'gpt-4o-mini'

logger = logging.getLogger(__name__)


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
    """Main app: serves AgentCard and mounts Gradio UI under /."""
    # ERC-8004: register Assistant agent identity (optional)
    try:
        assistant_pk = os.getenv('ERC8004_PRIVATE_KEY_ASSISTANT') or os.getenv('ERC8004_PRIVATE_KEY')
        adapter = Erc8004Adapter(private_key=assistant_pk)
        assistant_domain = (
            os.getenv('ERC8004_AGENT_DOMAIN_ASSISTANT')
            or os.getenv('ERC8004_AGENT_DOMAIN')
            or 'assistant.localhost:8083'
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
            type='messages',
        )

    # Build FastAPI app and mount Gradio under a non-root path to avoid '//' redirects
    app = FastAPI()

    @app.get('/.well-known/agent-card.json')
    def agent_card():
        host = os.environ.get('APP_HOST', '0.0.0.0')
        port = int(os.environ.get('APP_PORT', '8083'))
        app_url = os.environ.get('APP_URL', f'http://{host}:{port}')
        capabilities = AgentCapabilities(streaming=True)
        skill = AgentSkill(
            id='assistant',
            name='Travel assistant',
            description='Find and reserve places to stay and check weather',
            tags=['finder', 'reserve', 'weather'],
            examples=['Find a place in LA and reserve it, then check weather'],
        )
        # Base card
        card = AgentCard(
            name='assistant',
            description='Travel assistant for finding and booking stays and checking weather',
            url=app_url,
            version='1.0.0',
            default_input_modes=['text', 'text/plain'],
            default_output_modes=['text', 'text/plain'],
            capabilities=capabilities,
            skills=[skill],
        )
        card_dict = card.model_dump(exclude_none=True)

        # Augment with ERC-8004 registration and FeedbackDataURI
        try:
            if os.getenv('ERC8004_CARD_LOOKUP', 'false').lower() == 'true':
                adapter = Erc8004Adapter()
                domain = os.getenv('ERC8004_AGENT_DOMAIN_ASSISTANT') or os.getenv('ERC8004_AGENT_DOMAIN') or 'assistant.localhost:8083'
                info = adapter.get_agent_by_domain(domain)
                if info and info.get('agent_id') and info.get('address'):
                    chain_id = os.getenv('ERC8004_CHAIN_ID', '11155111')
                    caip10 = f"eip155:{chain_id}:{info['address']}"
                    # Optional ownership signature over domain
                    signature_hex = None
                    private_key = os.getenv('ERC8004_PRIVATE_KEY_ASSISTANT') or os.getenv('ERC8004_PRIVATE_KEY')
                    if private_key:
                        try:
                            from eth_account import Account
                            from eth_account.messages import encode_defunct

                            msg = encode_defunct(text=domain)
                            signed = Account.sign_message(msg, private_key=private_key)
                            signature_hex = signed.signature.hex()
                        except Exception:
                            signature_hex = None
                    reg = {
                        'agentId': int(info['agent_id']),
                        'agentAddress': caip10,
                    }
                    if signature_hex:
                        reg['signature'] = signature_hex
                    card_dict['registrations'] = [reg]
            # Feedback export URI (always present)
            card_dict['FeedbackDataURI'] = f"{app_url}/.well-known/feedback.json"
        except Exception:
            pass

        return card_dict

    @app.get('/.well-known/agent-ids')
    def agent_ids():
        adapter = Erc8004Adapter()
        logger.info(f'************ FINDER_DOMAIN: {os.getenv("FINDER_DOMAIN", "finder.localhost:10002")}')
        finder = adapter.get_agent_by_domain(os.getenv('FINDER_DOMAIN', 'finder.localhost:10002'))
        reserve = adapter.get_agent_by_domain(os.getenv('RESERVE_DOMAIN', 'reserve.localhost:10002'))
        return {'finder': finder, 'reserve': reserve}

    @app.get('/.well-known/feedback.json')
    def feedback_json():
        # Export in the requested format; gather from routing agent memory
        try:
            records = routing_agent.feedback_records if hasattr(routing_agent, 'feedback_records') else []
            return records
        except Exception:
            return []

    # Redirect root to the mounted Gradio UI path to avoid double-slash ('//') redirects
    @app.get('/')
    def root_redirect():
        return RedirectResponse(url='/ui')

    # Queue not supported in this Gradio version; leaving disabled
    gr.mount_gradio_app(app, demo, path='/ui')

    config = uvicorn.Config(app=app, host='0.0.0.0', port=8083, log_level='info')
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == '__main__':
    asyncio.run(main())

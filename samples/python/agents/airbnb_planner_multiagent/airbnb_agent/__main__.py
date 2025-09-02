# pylint: disable=logging-fstring-interpolation

import asyncio
import os
import sys

from contextlib import asynccontextmanager
from typing import Any

import click
import logging
import sys
import uvicorn

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
# Ensure intra-package imports work when run as a script
if __package__ is None or __package__ == '':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_executor import (
    AirbnbAgentExecutor,
)
from base_agent import BaseAgent
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from common_utils.erc8004_adapter import Erc8004Adapter
from eth_account.messages import encode_defunct


load_dotenv(override=True)

# Ensure INFO logs from agent modules are emitted
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

# Ensure prints flush immediately
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

SERVER_CONFIGS = {
    'bnb': {
        'command': 'npx',
        'args': ['-y', '@openbnb/mcp-server-airbnb', '--ignore-robots-txt'],
        'transport': 'stdio',
    },
}

app_context: dict[str, Any] = {}


DEFAULT_HOST = '0.0.0.0'
DEFAULT_PORT = 10002
DEFAULT_LOG_LEVEL = 'info'


@asynccontextmanager
async def app_lifespan(context: dict[str, Any]):
    """Manages the lifecycle of shared resources like the MCP client and tools."""
    print('Lifespan: Initializing MCP client and tools...')

    # This variable will hold the MultiServerMCPClient instance
    mcp_client_instance: MultiServerMCPClient | None = None

    try:
        # Following Option 1 from the error message for MultiServerMCPClient initialization:
        # 1. client = MultiServerMCPClient(...)
        mcp_client_instance = MultiServerMCPClient(SERVER_CONFIGS)
        mcp_tools = await mcp_client_instance.get_tools()
        context['mcp_tools'] = mcp_tools

        tool_count = len(mcp_tools) if mcp_tools else 0
        print(
            f'Lifespan: MCP Tools preloaded successfully ({tool_count} tools found).'
        )
        yield  # Application runs here
    except Exception as e:
        print(f'Lifespan: Error during initialization: {e}', file=sys.stderr)
        # If an exception occurs, mcp_client_instance might exist and need cleanup.
        # The finally block below will handle this.
        raise
    finally:
        print('Lifespan: Shutting down MCP client...')
        if (
            mcp_client_instance
        ):  # Check if the MultiServerMCPClient instance was created
            # The original code called __aexit__ on the MultiServerMCPClient instance
            # (which was mcp_client_manager). We assume this is still the correct cleanup method.
            if hasattr(mcp_client_instance, '__aexit__'):
                try:
                    print(
                        f'Lifespan: Calling __aexit__ on {type(mcp_client_instance).__name__} instance...'
                    )
                    await mcp_client_instance.__aexit__(None, None, None)
                    print(
                        'Lifespan: MCP Client resources released via __aexit__.'
                    )
                except Exception as e:
                    print(
                        f'Lifespan: Error during MCP client __aexit__: {e}',
                        file=sys.stderr,
                    )
            else:
                # This would be unexpected if only the context manager usage changed.
                # Log an error as this could lead to resource leaks.
                print(
                    f'Lifespan: CRITICAL - {type(mcp_client_instance).__name__} instance does not have __aexit__ method for cleanup. Resource leak possible.',
                    file=sys.stderr,
                )
        else:
            # This case means MultiServerMCPClient() constructor likely failed or was not reached.
            print(
                'Lifespan: MCP Client instance was not created, no shutdown attempt via __aexit__.'
            )

        # Clear the application context as in the original code.
        print('Lifespan: Clearing application context.')
        context.clear()


def main(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    log_level: str = DEFAULT_LOG_LEVEL,
    variant: str = 'finder',
):
    """Command Line Interface to start the Airbnb Agent server."""
    # Verify OpenAI API key is set.
    if not os.getenv('OPENAI_API_KEY'):
        raise ValueError('OPENAI_API_KEY environment variable not set.')

    async def run_server_async():
        async with app_lifespan(app_context):
            if not app_context.get('mcp_tools'):
                print(
                    'Warning: MCP tools were not loaded. Agent may not function correctly.',
                    file=sys.stderr,
                )
                # Depending on requirements, you could sys.exit(1) here

            # Build two inner apps (finder/reserve) and dispatch by Host header
            airbnb_agent_executor_finder = AirbnbAgentExecutor(
                mcp_tools=app_context.get('mcp_tools', []), variant='finder'
            )
            airbnb_agent_executor_reserve = AirbnbAgentExecutor(
                mcp_tools=app_context.get('mcp_tools', []), variant='reserve'
            )

            request_handler_finder = DefaultRequestHandler(
                agent_executor=airbnb_agent_executor_finder,
                task_store=InMemoryTaskStore(),
            )
            request_handler_reserve = DefaultRequestHandler(
                agent_executor=airbnb_agent_executor_reserve,
                task_store=InMemoryTaskStore(),
            )

            a2a_server_finder = A2AStarletteApplication(
                agent_card=get_agent_card(host, port, 'finder'),
                http_handler=request_handler_finder,
            )
            a2a_server_reserve = A2AStarletteApplication(
                agent_card=get_agent_card(host, port, 'reserve'),
                http_handler=request_handler_reserve,
            )

            inner_finder = a2a_server_finder.build()
            inner_reserve = a2a_server_reserve.build()

            async def app(scope, receive, send):
                if scope.get('type') != 'http':
                    return await inner_finder(scope, receive, send)
                headers = {k.decode().lower(): v.decode() for k, v in scope.get('headers', [])}
                host_header = headers.get('host', '')
                # Parse host without port
                host_only = host_header.split(':', 1)[0].lower()
                path = scope.get('path', '/')
                # Support prefixed agent-card paths to avoid DNS dependencies
                if path in ('/.well-known/agent-card.json', '/finder/.well-known/agent-card.json', '/reserve/.well-known/agent-card.json'):
                    # Compute card dynamically based on Host
                    server = scope.get('server') or (host_only, port)
                    req_port = server[1] if isinstance(server, (list, tuple)) and len(server) > 1 else port
                    if path.startswith('/reserve/'):
                        inferred_variant = 'reserve'
                    elif path.startswith('/finder/'):
                        inferred_variant = 'finder'
                    else:
                        inferred_variant = 'reserve' if host_only.startswith('reserve.') else 'finder'
                    card_dict = get_agent_card_dict(host_only, req_port, inferred_variant)
                    response = JSONResponse(card_dict)
                    return await response(scope, receive, send)
                # Route all other requests to the appropriate inner app
                if path.startswith('/reserve'):
                    # strip prefix for inner app
                    scope2 = dict(scope)
                    scope2['path'] = path[len('/reserve'):] or '/'
                    return await inner_reserve(scope2, receive, send)
                if path.startswith('/finder'):
                    scope2 = dict(scope)
                    scope2['path'] = path[len('/finder'):] or '/'
                    return await inner_finder(scope2, receive, send)
                target = inner_reserve if host_only.startswith('reserve.') else inner_finder
                return await target(scope, receive, send)

            config = uvicorn.Config(
                app=app,
                host=host,
                port=port,
                log_level=log_level.lower(),
                lifespan='auto',
            )

            uvicorn_server = uvicorn.Server(config)

            print(
                f'Starting Uvicorn server at http://{host}:{port} [{variant}] with log-level {log_level}...'
            )
            try:
                await uvicorn_server.serve()
            except KeyboardInterrupt:
                print('Server shutdown requested (KeyboardInterrupt).')
            finally:
                print('Uvicorn server has stopped.')
                # The app_lifespan's finally block handles mcp_client shutdown

    try:
        asyncio.run(run_server_async())
    except RuntimeError as e:
        if 'cannot be called from a running event loop' in str(e):
            print(
                'Critical Error: Attempted to nest asyncio.run(). This should have been prevented.',
                file=sys.stderr,
            )
        else:
            print(f'RuntimeError in main: {e}', file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f'An unexpected error occurred in main: {e}', file=sys.stderr)
        sys.exit(1)


def get_agent_card(host: str, port: int, variant: str):
    """Returns the Agent Card for the Currency Agent."""
    capabilities = AgentCapabilities(streaming=True, push_notifications=True)
    if variant == 'reserve':
        skill = AgentSkill(
            id='reserve',
            name='Reserve accommodation',
            description='Assists with reserving an Airbnb listing',
            tags=['airbnb reserve'],
            examples=['Reserve this listing for 2 adults from 15 Apr to 18 Apr'],
        )
        agent_name = 'Airbnb Agent - Reserve'
        agent_desc = 'Helps with reserving accommodation'
    else:
        skill = AgentSkill(
            id='finder',
            name='Find accommodation',
            description='Helps with searching Airbnb listings',
            tags=['airbnb search'],
            examples=['Find a room in LA, CA, Apr 15–18, 2 adults'],
        )
        agent_name = 'Airbnb Agent - Finder'
        agent_desc = 'Helps with searching accommodation'
    app_url = os.environ.get('APP_URL', f'http://{host}:{port}')

    return AgentCard(
        name=agent_name,
        description=agent_desc,
        url=app_url,
        version='1.0.0',
        default_input_modes=BaseAgent.SUPPORTED_CONTENT_TYPES,
        default_output_modes=BaseAgent.SUPPORTED_CONTENT_TYPES,
        capabilities=capabilities,
        skills=[skill],
    )


def get_agent_card_dict(host: str, port: int, variant: str) -> dict[str, Any]:
    """Build AgentCard dict and augment with ERC-8004 registration and trust models."""
    base_card = get_agent_card(host, port, variant)
    card_dict = base_card.model_dump(exclude_none=True)

    registration = _build_erc8004_registration(port, variant)
    if registration is not None:
        card_dict['registrations'] = [registration]

    trust_models_env = os.getenv('ERC8004_TRUST_MODELS', 'feedback')
    trust_models = [m.strip() for m in trust_models_env.split(',') if m.strip()]
    if trust_models:
        card_dict['trustModels'] = trust_models

    return card_dict


def _build_erc8004_registration(port: int, variant: str) -> dict[str, Any] | None:
    """Create ERC-8004 registration object with agentId, CAIP-10 address, and signature.

    Signature is over the agent domain name.
    """
    try:
        adapter = Erc8004Adapter()

        # Determine domain for lookup/signing
        if variant == 'reserve':
            domain = os.getenv('RESERVE_DOMAIN') or os.getenv('ERC8004_AGENT_DOMAIN_RESERVE') or f'reserve.localhost:{port}'
            private_key = os.getenv('ERC8004_PRIVATE_KEY_RESERVE')
        else:
            domain = os.getenv('FINDER_DOMAIN') or os.getenv('ERC8004_AGENT_DOMAIN_FINDER') or f'finder.localhost:{port}'
            private_key = os.getenv('ERC8004_PRIVATE_KEY_FINDER')

        info = adapter.get_agent_by_domain(domain)
        if not info:
            return None

        agent_id_val = int(info.get('agent_id', 0)) if info.get('agent_id') is not None else 0
        agent_eth_addr = str(info.get('address') or '').strip()
        if agent_id_val <= 0 or not agent_eth_addr:
            return None

        # CAIP-10 address with Sepolia default (eip155:11155111)
        chain_id = os.getenv('ERC8004_CHAIN_ID', '11155111')
        caip10_addr = f'eip155:{chain_id}:{agent_eth_addr}'

        registration: dict[str, Any] = {
            'agentId': agent_id_val,
            'agentAddress': caip10_addr,
        }

        # Optional ownership signature over the domain
        if private_key:
            try:
                from eth_account import Account

                msg = encode_defunct(text=domain)
                signed = Account.sign_message(msg, private_key=private_key)
                registration['signature'] = signed.signature.hex()
            except Exception:
                pass

        return registration
    except Exception:
        return None


@click.command()
@click.option(
    '--host',
    'host',
    default=DEFAULT_HOST,
    help='Hostname to bind the server to.',
)
@click.option(
    '--port',
    'port',
    default=DEFAULT_PORT,
    type=int,
    help='Port to bind the server to.',
)
@click.option(
    '--log-level',
    'log_level',
    default=DEFAULT_LOG_LEVEL,
    help='Uvicorn log level.',
)
@click.option(
    '--variant',
    'variant',
    type=click.Choice(['finder', 'reserve']),
    default='finder',
    help='Agent variant to run at this endpoint.',
)
def cli(host: str, port: int, log_level: str, variant: str):
    main(host, port, log_level, variant)


if __name__ == '__main__':
    main()

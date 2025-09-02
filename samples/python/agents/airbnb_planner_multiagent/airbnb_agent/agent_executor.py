# pylint: disable=logging-fstring-interpolation
import logging
import os

from typing import Any, override

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import new_agent_text_message, new_task, new_text_artifact
from finder_agent import FinderAgent
from reserve_agent import ReserveAgent
from common_utils.erc8004_adapter import Erc8004Adapter


logger = logging.getLogger(__name__)


class AirbnbAgentExecutor(AgentExecutor):
    """AirbnbAgentExecutor that uses an agent with preloaded tools."""

    def __init__(self, mcp_tools: list[Any], variant: str = 'finder'):
        """Initializes the AirbnbAgentExecutor.

        Args:
            mcp_tools: A list of preloaded MCP tools for the AirbnbAgent.
        """
        super().__init__()
        logger.info(
            f'Initializing AirbnbAgentExecutor with {len(mcp_tools) if mcp_tools else "no"} MCP tools.'
        )
        self.agent = (
            ReserveAgent(mcp_tools) if variant == 'reserve' else FinderAgent(mcp_tools)
        )

    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        query = context.get_user_input()
        task = context.current_task

        print('EXECUTE print reached:', query)
        logger.info('ERC-8004: execute query: %s', query)
        logger.warning('EXECUTE REACHED: query=%s', query)

        if not context.message:
            raise Exception('No message provided')

        if not task:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)

        logger.info('ERC-8004: execute task: %s, %s', task, context.message)
        # Server-side authorization: if caller provided client_agent_id, authorize now
        try:
            client_id_meta = None
            if context.message and hasattr(context.message, 'metadata'):
                client_id_meta = (context.message.metadata or {}).get('client_agent_id')
            logger.info('ERC-8004: client_agent_id from metadata: %s', client_id_meta)
            if client_id_meta and str(client_id_meta).strip():
                adapter = Erc8004Adapter()
                # Resolve server agent id by variant
                server_domain = 'reserve' if isinstance(self.agent, ReserveAgent) else 'finder'
                domain_env = 'RESERVE_DOMAIN' if isinstance(self.agent, ReserveAgent) else 'FINDER_DOMAIN'
                domain_val = os.getenv(domain_env)
                if not domain_val:
                    # Fallback to variant label if domain not set
                    domain_val = server_domain
                server_info = adapter.get_agent_by_domain(domain_val)
                if server_info and server_info.get('agent_id'):
                    signing_key = (
                        os.getenv('ERC8004_PRIVATE_KEY_RESERVE') if isinstance(self.agent, ReserveAgent)
                        else os.getenv('ERC8004_PRIVATE_KEY_FINDER')
                    ) or os.getenv('ERC8004_PRIVATE_KEY')
                    logger.info(
                        'ERC-8004: authorize_feedback (client=%s, server=%s) using server key variant=%s',
                        client_id_meta,
                        server_info['agent_id'],
                        'reserve' if isinstance(self.agent, ReserveAgent) else 'finder',
                    )
                    auth_res = adapter.authorize_feedback_from_client(
                        client_agent_id=int(client_id_meta),
                        server_agent_id=int(server_info['agent_id']),
                        signing_private_key=signing_key,
                    )
                    logger.info('ERC-8004: authorize_feedback result: %s', auth_res)
        except Exception as e:
            logger.info('ERC-8004: server-side authorize_feedback failed: %s', e)

        # invoke the underlying agent, using streaming results
        async for event in self.agent.stream(query, task.context_id):
            if event['is_task_complete']:
                await event_queue.enqueue_event(
                    TaskArtifactUpdateEvent(
                        append=False,
                        context_id=task.context_id,
                        task_id=task.id,
                        last_chunk=True,
                        artifact=new_text_artifact(
                            name='current_result',
                            description='Result of request to agent.',
                            text=event['content'],
                        ),
                    )
                )
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        status=TaskStatus(state=TaskState.completed),
                        final=True,
                        context_id=task.context_id,
                        task_id=task.id,
                    )
                )
            elif event['require_user_input']:
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        status=TaskStatus(
                            state=TaskState.input_required,
                            message=new_agent_text_message(
                                event['content'],
                                task.context_id,
                                task.id,
                            ),
                        ),
                        final=True,
                        context_id=task.context_id,
                        task_id=task.id,
                    )
                )
            else:
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        status=TaskStatus(
                            state=TaskState.working,
                            message=new_agent_text_message(
                                event['content'],
                                task.context_id,
                                task.id,
                            ),
                        ),
                        final=False,
                        context_id=task.context_id,
                        task_id=task.id,
                    )
                )

    @override
    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise Exception('cancel not supported')

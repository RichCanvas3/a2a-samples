from typing import Any

from base_agent import BaseAgent


class FinderAgent(BaseAgent):
    """Thin wrapper for the Finder variant of the Airbnb agent."""

    def __init__(self, mcp_tools: list[Any]):
        super().__init__(mcp_tools=mcp_tools, variant='finder')



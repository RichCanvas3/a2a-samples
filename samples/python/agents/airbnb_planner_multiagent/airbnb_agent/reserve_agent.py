from typing import Any

from base_agent import BaseAgent
from langchain_core.tools import tool
from pydantic import BaseModel
import uuid
from erc8004_adapter import Erc8004Adapter


class ReserveRequest(BaseModel):
    listing_url: str
    check_in: str
    check_out: str
    guests: int = 1


@tool("reserve_listing", args_schema=ReserveRequest)
def reserve_listing(listing_url: str, check_in: str, check_out: str, guests: int = 1) -> str:
    """Reserve a specific Airbnb listing (mock implementation).

    Provide: listing_url, check_in (YYYY-MM-DD), check_out (YYYY-MM-DD), guests.
    Returns a confirmation with a mock booking ID and echo of the inputs.
    """
    booking_id = uuid.uuid4().hex[:10]
    try:
        adapter = Erc8004Adapter()
        adapter.record_reservation(
            {
                'booking_id': booking_id,
                'listing_url': listing_url,
                'check_in': check_in,
                'check_out': check_out,
                'guests': guests,
            }
        )
    except Exception:
        pass
    return (
        f"Reservation confirmed.\n"
        f"Booking ID: {booking_id}\n"
        f"Listing: {listing_url}\n"
        f"Check-in: {check_in}\n"
        f"Check-out: {check_out}\n"
        f"Guests: {guests}"
    )


class ReserveAgent(BaseAgent):
    """Reserve variant of the Airbnb agent with reservation tool."""

    def __init__(self, mcp_tools: list[Any]):
        super().__init__(mcp_tools=mcp_tools, variant='reserve')
        # Extend prompt and tools for reservation behavior
        self.SYSTEM_INSTRUCTION += (
            "\n\nReservation mode: If the user asks to reserve one of the previously presented listings, "
            "use the reserve_listing tool. Extract listing URL (or infer from context), check-in, check-out, and guests. "
            "Confirm the reservation details in Markdown, including the booking ID returned by the tool."
        )

    def get_tools(self) -> list[Any]:
        tools = super().get_tools()
        tools.append(reserve_listing)
        return tools



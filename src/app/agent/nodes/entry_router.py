"""`entry_router`: new turn bookkeeping and the first routing decision."""

from typing import Any

from app.agent.state import SupportState, turn_reset


async def entry_router(state: SupportState) -> dict[str, Any]:
    return turn_reset()


def route_entry(state: SupportState) -> str:
    # Every turn passes `guard_input` (length limit, PII masking), even when it
    # answers a question of the refund flow (`awaiting`).
    return "guard_input"

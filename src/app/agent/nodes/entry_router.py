"""`entry_router`: new turn bookkeeping and the first routing decision."""

from typing import Any

from app.agent.state import SupportState, turn_reset


async def entry_router(state: SupportState) -> dict[str, Any]:
    return turn_reset()


def route_entry(state: SupportState) -> str:
    # Spec 10 sends `awaiting` turns to `collect_refund_info`; until then the
    # stub reclassifies the message.
    return "classify_intent" if state.get("awaiting") else "guard_input"

"""`request_confirmation` (HITL): `interrupt()` is the first thing the node runs.

LangGraph re-executes the node on resume, so nothing with effects may precede the
interrupt: the card was built beforehand by `prepare_confirmation` and is only read
from state here.
"""

from datetime import datetime
from typing import Any

from langgraph.types import interrupt

from app.agent.deps import AgentDeps
from app.agent.prompts import CONFIRM_CANCELLED, CONFIRM_EDIT
from app.agent.state import SupportState

DECISIONS = ("confirm", "cancel", "edit")


def valid_resume(value: Any, interrupt_id: str) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("interrupt_id") != interrupt_id:
        return None
    if value.get("decision") not in DECISIONS:
        return None
    return value


def make_request_confirmation(deps: AgentDeps):  # type: ignore[no-untyped-def]
    async def request_confirmation(state: SupportState) -> dict[str, Any]:
        card = state["pending_confirmation"]
        assert card is not None
        value = interrupt(card)

        # --- resumed: pure decisions only below ---
        answer = valid_resume(value, card["interrupt_id"])
        if answer is None or answer["decision"] == "cancel":
            return _closed(CONFIRM_CANCELLED)
        if answer["decision"] == "edit":
            edited = (answer.get("edited_reason") or "").strip()
            update = _closed("")
            update["draft"] = "" if edited else CONFIRM_EDIT
            update["eligibility"] = state.get("eligibility")
            if edited:
                update.update(
                    reason_text=edited,
                    reason_edited=True,
                    confirmation_route="collect",
                )
            else:
                update.update(
                    reason_text=None, awaiting="reason", confirmation_route="end"
                )
            return update
        expires_at = card.get("expires_at")
        if expires_at and deps.clock() > datetime.fromisoformat(str(expires_at)):
            update = _closed("")
            update["confirmation_route"] = "recheck"
            return update
        return {"confirmation_route": "confirm"}

    return request_confirmation


def _closed(draft: str) -> dict[str, Any]:
    return {
        "draft": draft,
        "pending_confirmation": None,
        "confirmation_token": None,
        "confirmation_hash": None,
        "confirmation_route": "end",
        "awaiting": None,
    }


def route_confirmation(state: SupportState) -> str:
    match state.get("confirmation_route"):
        case "confirm":
            return "create_refund"
        case "collect":
            return "collect_refund_info"
        case "recheck":
            return "check_eligibility"
        case _:
            return "guard_output"

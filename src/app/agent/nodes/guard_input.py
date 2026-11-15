"""`guard_input`: length limit and basic PII masking (injection detector: spec 12)."""

import re
from typing import Any

from langchain_core.messages import HumanMessage

from app.agent.prompts import BLOCKED_TOO_LONG
from app.agent.state import SupportState

MAX_INPUT_CHARS = 2000
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def mask_pii(text: str) -> tuple[str, bool]:
    masked = _EMAIL.sub("[email]", text)
    masked = _CARD.sub("[número]", masked)
    return masked, masked != text


async def guard_input(state: SupportState) -> dict[str, Any]:
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if not isinstance(last, HumanMessage):
        return {"blocked": True, "draft": BLOCKED_TOO_LONG}
    text = str(last.content)
    if len(text) > MAX_INPUT_CHARS:
        return {
            "blocked": True,
            "draft": BLOCKED_TOO_LONG,
            "guardrail_flags": [*state.get("guardrail_flags", []), "input_too_long"],
        }
    masked, changed = mask_pii(text)
    update: dict[str, Any] = {"blocked": False}
    if changed:
        # Same id: `add_messages` replaces the stored message, so PII is not kept.
        update["messages"] = [HumanMessage(content=masked, id=last.id)]
        update["guardrail_flags"] = [*state.get("guardrail_flags", []), "pii_masked"]
    return update


def route_guard(state: SupportState) -> str:
    if state.get("blocked"):
        return "respond"
    # The previous turn asked the customer something: the answer goes to the flow.
    match state.get("awaiting"):
        case "purchase" | "reason":
            return "collect_refund_info"
        case "refund":
            return "cancel_refund_flow"
        case _:
            return "classify_intent"

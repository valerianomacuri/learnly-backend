"""`guard_input`: length limit, injection detector and PII masking (specs 09, 12)."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage

from app.agent.deps import AgentDeps
from app.agent.guardrails.injection import Level, assess
from app.agent.guardrails.input import MAX_INPUT_CHARS, judge_weak_signal
from app.agent.guardrails.pii import mask_pii
from app.agent.prompts import BLOCKED_INJECTION, BLOCKED_TOO_LONG
from app.agent.state import SupportState

logger = logging.getLogger("app.agent.guardrails")

__all__ = ["MAX_INPUT_CHARS", "make_guard_input", "mask_pii", "route_guard"]


def make_guard_input(
    deps: AgentDeps,
) -> Callable[[SupportState], Awaitable[dict[str, Any]]]:
    async def guard_input(state: SupportState) -> dict[str, Any]:
        messages = state.get("messages", [])
        last = messages[-1] if messages else None
        flags = list(state.get("guardrail_flags", []))
        if not isinstance(last, HumanMessage):
            return {"blocked": True, "draft": BLOCKED_TOO_LONG}
        text = str(last.content)
        if len(text) > MAX_INPUT_CHARS:
            return {
                "blocked": True,
                "draft": BLOCKED_TOO_LONG,
                "guardrail_flags": [*flags, "input_too_long"],
            }
        verdict = assess(text)
        blocked = verdict.level is Level.STRONG
        if verdict.level is Level.WEAK:
            blocked = await judge_weak_signal(deps.llm, text, deps.timed)
        if blocked:
            logger.warning("input blocked by injection guard: %s", verdict.matches)
            return {
                "blocked": True,
                "draft": BLOCKED_INJECTION,
                "guardrail_flags": [*flags, "prompt_injection_blocked"],
            }
        masked, changed = mask_pii(text)
        update: dict[str, Any] = {"blocked": False}
        if changed:
            # Same id: `add_messages` replaces the stored message, so PII is not kept.
            update["messages"] = [HumanMessage(content=masked, id=last.id)]
            update["guardrail_flags"] = [*flags, "pii_masked"]
        return update

    return guard_input


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

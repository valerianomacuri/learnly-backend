"""`classify_intent`: structured LLM output (T=0); keywords after two failures."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agent.deps import AgentDeps, last_user_text
from app.agent.nodes.keyword_classifier import classify_by_keywords
from app.agent.prompts import CLASSIFIER_SYSTEM
from app.agent.state import Intent, SupportState

logger = logging.getLogger("app.agent.classify")
LLM_ATTEMPTS = 2


class IntentResult(BaseModel):
    intent: Intent
    course_name: str | None = None


def make_classify_intent(
    deps: AgentDeps,
) -> Callable[[SupportState], Awaitable[dict[str, Any]]]:
    async def classify_intent(state: SupportState) -> dict[str, Any]:
        text = last_user_text(state.get("messages", []))
        prompt = [
            SystemMessage(CLASSIFIER_SYSTEM),
            HumanMessage(f"<customer_message>\n{text}\n</customer_message>"),
        ]
        runnable = deps.llm.with_structured_output(IntentResult)
        flags: list[str] = list(state.get("guardrail_flags", []))
        for attempt in range(1, LLM_ATTEMPTS + 1):
            try:
                raw = await deps.timed(runnable.ainvoke(prompt))
                result = (
                    raw
                    if isinstance(raw, IntentResult)
                    else IntentResult.model_validate(raw)
                )
                return {
                    "intent": result.intent.value,
                    "retries": attempt - 1,
                    "guardrail_flags": flags,
                }
            except Exception as exc:
                logger.warning("classifier attempt %d failed: %r", attempt, exc)
        flags.append("classifier_degraded")
        return {
            "intent": classify_by_keywords(text).value,
            "retries": LLM_ATTEMPTS,
            "guardrail_flags": flags,
        }

    return classify_intent


def route_intent(state: SupportState) -> str:
    match state.get("intent"):
        case Intent.POLICY_QUESTION:
            return "policy_qa"
        case Intent.REFUND_STATUS:
            return "refund_status"
        case Intent.REFUND_REQUEST | Intent.CANCEL_REFUND:
            return "refund_form_notice"
        case _:  # out_of_scope, human_handoff or unknown
            return "handoff"

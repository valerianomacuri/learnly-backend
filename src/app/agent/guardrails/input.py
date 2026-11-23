"""LLM judge for suspicious-but-plausible messages (spec 12)."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.guardrails.injection import JUDGE_SYSTEM, InjectionVerdict
from app.core.untrusted import wrap_untrusted

logger = logging.getLogger("app.agent.guardrails")

MAX_INPUT_CHARS = 2000

Timed = Callable[[Awaitable[Any]], Awaitable[Any]]


async def judge_weak_signal(llm: BaseChatModel, text: str, timed: Timed) -> bool:
    """True if the cheap LLM judge says `text` is an injection attempt.

    Any failure (LLM down, breaker open, bad output) trusts the heuristics: a weak
    signal alone never blocks a customer, and the agent has no tool a miss could abuse.
    """
    try:
        runnable = llm.with_structured_output(InjectionVerdict)
        raw = await timed(
            runnable.ainvoke(
                [
                    SystemMessage(JUDGE_SYSTEM),
                    HumanMessage(wrap_untrusted(text, "customer")),
                ]
            )
        )
        verdict = (
            raw
            if isinstance(raw, InjectionVerdict)
            else InjectionVerdict.model_validate(raw)
        )
    except Exception as exc:
        logger.warning("injection judge unavailable: %r", exc)
        return False
    return verdict.is_injection

"""`guard_output`: nothing internal or foreign reaches the customer (challenge §2.5).

Deterministic filters (`agent/guardrails/output.py`): internal-note vocabulary or a
recited system prompt block the answer, emails are masked, ids next to supervisor
wording are redacted, a policy claim without citations is replaced by an
abstention, and day/percentage figures must match the active policy.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.agent.deps import AgentDeps
from app.agent.guardrails.output import POLICY_CLAIM, filter_output
from app.agent.prompts import SAFE_FALLBACK, UNCITED_POLICY
from app.agent.state import Intent, SupportState
from app.rag.answer import unverified_numbers
from app.repositories.policy import PolicyRepository

logger = logging.getLogger("app.agent.guardrails")

__all__ = ["filter_output", "make_guard_output"]


def make_guard_output(
    deps: AgentDeps,
) -> Callable[[SupportState], Awaitable[dict[str, Any]]]:
    async def _unsupported_figures(text: str) -> list[str]:
        try:
            async with deps.session_maker() as session:
                policies = PolicyRepository(session)
                config = await policies.get_active_config()
                trusted = await policies.get_trusted_figures()
        except Exception:
            logger.warning(
                "policy config unavailable for the figure check", exc_info=True
            )
            return []
        return unverified_numbers(text, config, [], trusted)

    async def guard_output(state: SupportState) -> dict[str, Any]:
        text, flags = filter_output(state.get("draft", ""))
        flags = [*state.get("guardrail_flags", []), *flags]
        if (
            state.get("intent") == Intent.POLICY_QUESTION
            and not state.get("needs_handoff")
            and not state.get("handoff_reason")
            and not state.get("citations")
            and POLICY_CLAIM.search(text)
        ):
            text = UNCITED_POLICY
            flags.append("uncited_policy_claim")
        elif text != SAFE_FALLBACK and await _unsupported_figures(text):
            text = SAFE_FALLBACK
            flags.append("unsupported_figure_blocked")
        return {"draft": text, "guardrail_flags": flags}

    return guard_output

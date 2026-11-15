"""`explain_ineligible`: narrates the reason codes with deterministic citations.

Citations come from `reason_map.citations_for` (config-driven, no LLM); a semantic
search adds context for the narration. Writes nothing.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from app.agent.deps import AgentDeps, last_user_text
from app.agent.prompts import EXPLAIN_SYSTEM, NOT_ELIGIBLE_FALLBACK
from app.agent.state import SupportState
from app.rag.reason_map import citations_for
from app.repositories.policy import PolicyRepository

logger = logging.getLogger("app.agent.explain")


def make_explain_ineligible(
    deps: AgentDeps,
) -> Callable[[SupportState, RunnableConfig], Awaitable[dict[str, Any]]]:
    async def explain_ineligible(
        state: SupportState, config: RunnableConfig
    ) -> dict[str, Any]:
        eligibility = state.get("eligibility") or {}
        codes = [str(c) for c in eligibility.get("reason_codes", [])]
        extra = ""
        async with deps.session_maker() as session:
            policy = await PolicyRepository(session).get_active_config()
            citations = citations_for(policy, codes)
            try:
                found = await deps.retriever_factory(session).retrieve(
                    " ".join(codes) or last_user_text(state.get("messages", []))
                )
                extra = "\n".join(c.content for c in found.chunks[:3])
            except Exception:  # complementary only
                logger.warning("semantic search failed", exc_info=True)
        sections = "\n".join(f"- {c.section_path}" for c in citations)
        prompt = [
            SystemMessage(EXPLAIN_SYSTEM),
            HumanMessage(
                f"<context>\nCódigos: {', '.join(codes)}\nSecciones:\n{sections}\n"
                f"{extra}\n</context>"
            ),
        ]
        try:
            reply = await deps.timed(deps.llm.ainvoke(prompt))
            text = reply.content if isinstance(reply, AIMessage) else ""
            text = text if isinstance(text, str) else ""
        except Exception:
            logger.warning("explain narration failed", exc_info=True)
            text = ""
        return {
            "draft": text or NOT_ELIGIBLE_FALLBACK.format(codes=", ".join(codes)),
            "citations": [c.model_dump() for c in citations],
            "awaiting": None,
            "pending_confirmation": None,
            "confirmation_token": None,
        }

    return explain_ineligible

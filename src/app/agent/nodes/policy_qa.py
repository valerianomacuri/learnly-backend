"""`policy_qa`: grounded answer with citations through `rag.answer` (spec 07)."""

from collections.abc import Awaitable, Callable
from typing import Any

from app.agent.deps import AgentDeps, last_user_text
from app.agent.llm import LangChainChat
from app.agent.state import SupportState
from app.rag.answer import PolicyAnswerer
from app.repositories.policy import PolicyRepository


def make_policy_qa(
    deps: AgentDeps,
) -> Callable[[SupportState], Awaitable[dict[str, Any]]]:
    async def policy_qa(state: SupportState) -> dict[str, Any]:
        messages = state.get("messages", [])
        question = last_user_text(messages)
        history = tuple(
            str(m.content) for m in messages[:-1] if getattr(m, "type", "") == "human"
        )[-3:]
        async with deps.session_maker() as session:
            policies = PolicyRepository(session)
            config = await policies.get_active_config()
            trusted = await policies.get_trusted_figures()
            answerer = PolicyAnswerer(
                deps.retriever_factory(session),
                LangChainChat(deps.llm),
                config,
                trusted,
            )
            result = await deps.timed(answerer.answer(question, history=history))
        answer = result.answer
        return {
            "draft": answer.answer,
            "citations": [c.model_dump() for c in answer.citations],
            "needs_handoff": answer.needs_handoff,
            "handoff_reason": "policy_no_evidence" if answer.needs_handoff else "",
        }

    return policy_qa


def route_policy(state: SupportState) -> str:
    return "handoff" if state.get("needs_handoff") else "guard_output"

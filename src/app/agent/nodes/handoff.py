"""`handoff`: deterministic escalation (tool 10) and the message that explains it."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.agent.deps import AgentDeps, last_user_text
from app.agent.prompts import HANDOFF_FAILED, HANDOFF_NOTICE, OUT_OF_SCOPE_NOTICE
from app.agent.state import Intent, SupportState
from app.agent.tools.base import run_tool
from app.agent.tools.registry import ALL_TOOLS


def make_handoff(
    deps: AgentDeps,
) -> Callable[[SupportState, RunnableConfig], Awaitable[dict[str, Any]]]:
    async def handoff(state: SupportState, config: RunnableConfig) -> dict[str, Any]:
        intent = state.get("intent")
        prefix = state.get("draft", "")
        if not prefix and intent == Intent.OUT_OF_SCOPE:
            prefix = OUT_OF_SCOPE_NOTICE
        category = "policy" if intent == Intent.POLICY_QUESTION else "other"
        summary = last_user_text(state.get("messages", []))[:500] or "Sin resumen"
        args = {"summary": summary, "category": category}

        emit = get_stream_writer()
        emit({"type": "tool_call", "name": "escalate_to_human", "args": args})
        result = await run_tool(
            ALL_TOOLS["escalate_to_human"], deps.tool_context(config), args
        )
        emit(
            {
                "type": "tool_result",
                "name": "escalate_to_human",
                "ok": result.ok,
                "error_code": result.error.code if result.error else None,
            }
        )
        notice = HANDOFF_NOTICE if result.ok else HANDOFF_FAILED
        return {
            "draft": f"{prefix}\n\n{notice}" if prefix else notice,
            "needs_handoff": True,
        }

    return handoff

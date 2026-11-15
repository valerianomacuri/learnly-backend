"""`check_eligibility`: deterministic. Tool 4; branches eligible / not eligible."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.agent.deps import AgentDeps
from app.agent.prompts import REFUND_FAILED
from app.agent.state import SupportState
from app.agent.tools.base import run_tool
from app.agent.tools.registry import ALL_TOOLS


def make_check_eligibility(
    deps: AgentDeps,
) -> Callable[[SupportState, RunnableConfig], Awaitable[dict[str, Any]]]:
    async def check_eligibility(
        state: SupportState, config: RunnableConfig
    ) -> dict[str, Any]:
        args = {"purchase_id": state.get("selected_purchase_id")}
        emit = get_stream_writer()
        emit({"type": "tool_call", "name": "check_refund_eligibility", "args": args})
        result = await run_tool(
            ALL_TOOLS["check_refund_eligibility"], deps.tool_context(config), args
        )
        emit(
            {
                "type": "tool_result",
                "name": "check_refund_eligibility",
                "ok": result.ok,
                "error_code": result.error.code if result.error else None,
            }
        )
        if not result.ok:
            if result.error and result.error.retryable:
                raise ConnectionError(result.error.code)  # retried by policy
            return {"eligibility": None, "draft": REFUND_FAILED}
        return {"eligibility": result.data, "draft": ""}

    return check_eligibility


def route_eligibility(state: SupportState) -> str:
    eligibility = state.get("eligibility")
    if eligibility is None:
        return "guard_output"
    return (
        "prepare_confirmation" if eligibility.get("eligible") else "explain_ineligible"
    )

"""`create_refund` (deterministic): tool 6 with arguments taken from the state.

Only reachable from `request_confirmation` after an explicit `confirm`. The
Idempotency-Key is `sha256(thread_id + hash)`: a retry of this node (or a replayed
resume) maps to the same request and the service returns the original.
"""

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.agent.deps import AgentDeps
from app.agent.principal import AgentPrincipal
from app.agent.prompts import REFUND_CREATED, REFUND_FAILED
from app.agent.state import SupportState
from app.agent.tools.base import run_tool
from app.agent.tools.registry import ALL_TOOLS

CLEARED: dict[str, Any] = {
    "pending_confirmation": None,
    "confirmation_token": None,
    "confirmation_hash": None,
    "awaiting": None,
    "confirmation_route": "end",
}


def idempotency_key(thread_id: str, payload_hash: str) -> str:
    return hashlib.sha256((thread_id + payload_hash).encode()).hexdigest()


def make_create_refund(
    deps: AgentDeps,
) -> Callable[[SupportState, RunnableConfig], Awaitable[dict[str, Any]]]:
    async def create_refund(
        state: SupportState, config: RunnableConfig
    ) -> dict[str, Any]:
        ctx = deps.tool_context(config)
        thread_id = AgentPrincipal.from_config(config).thread_id
        args = {
            "purchase_id": state.get("selected_purchase_id"),
            "reason_category": state.get("reason_category"),
            "reason_text": state.get("reason_text"),
            "confirmation_proof": state.get("confirmation_token"),
            "idempotency_key": idempotency_key(
                thread_id, state.get("confirmation_hash") or ""
            ),
        }
        emit = get_stream_writer()
        emit(
            {
                "type": "tool_call",
                "name": "create_refund_request",
                "args": {k: v for k, v in args.items() if k != "confirmation_proof"},
            }
        )
        result = await run_tool(ALL_TOOLS["create_refund_request"], ctx, args)
        emit(
            {
                "type": "tool_result",
                "name": "create_refund_request",
                "ok": result.ok and bool(result.data and result.data.get("created")),
                "error_code": result.error.code if result.error else None,
            }
        )
        if not result.ok:
            if result.error and result.error.retryable:
                raise ConnectionError(result.error.code)  # retried: same key
            code = result.error.code if result.error else ""
            if code == "CONFIRMATION_INVALID":
                return {**CLEARED, "draft": "", "confirmation_route": "recheck"}
            return {**CLEARED, "draft": REFUND_FAILED}
        data = result.data
        if data.get("created"):
            return {
                **CLEARED,
                "refund_id": str(data["refund_id"]),
                "draft": REFUND_CREATED.format(
                    amount=data["amount"], currency=data["currency"]
                ),
            }
        if data.get("error") == "REFUND_NOT_ELIGIBLE":
            eligibility = dict(state.get("eligibility") or {})
            eligibility.update(eligible=False, reason_codes=data["reason_codes"])
            return {
                **CLEARED,
                "eligibility": eligibility,
                "draft": "",
                "confirmation_route": "explain",
            }
        return {**CLEARED, "draft": REFUND_FAILED}

    return create_refund


def route_created(state: SupportState) -> str:
    match state.get("confirmation_route"):
        case "explain":
            return "explain_ineligible"
        case "recheck":
            return "check_eligibility"
        case _:
            return "guard_output"

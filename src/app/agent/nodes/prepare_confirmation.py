"""`prepare_confirmation`: builds the card with tool 5 (deterministic, no LLM).

Kept apart from `request_confirmation` because `interrupt()` re-executes its node on
resume: the node that interrupts must have no effects, and `preview` records an
eligibility check. The signed token goes to the state, never to the card or a message.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.agent.deps import AgentDeps
from app.agent.prompts import REFUND_FAILED
from app.agent.state import SupportState
from app.agent.tools.base import ToolArgs
from app.agent.tools.eligibility import PrepareConfirmationArgs, prepare
from app.agent.tools.registry import ALL_TOOLS


def make_prepare_confirmation(
    deps: AgentDeps,
) -> Callable[[SupportState, RunnableConfig], Awaitable[dict[str, Any]]]:
    async def prepare_confirmation(
        state: SupportState, config: RunnableConfig
    ) -> dict[str, Any]:
        ctx = deps.tool_context(config)
        args: ToolArgs = PrepareConfirmationArgs(
            purchase_id=state["selected_purchase_id"],  # type: ignore[arg-type]
            reason_category=state["reason_category"],  # type: ignore[arg-type]
            reason_text=state["reason_text"] or "",
        )
        emit = get_stream_writer()
        spec = ALL_TOOLS["prepare_refund_confirmation"]
        emit(
            {
                "type": "tool_call",
                "name": spec.name,
                "args": args.model_dump(mode="json"),
            }
        )
        try:
            prepared = await prepare(ctx, args)  # type: ignore[arg-type]
        except ConnectionError:
            raise
        except Exception:
            emit(
                {
                    "type": "tool_result",
                    "name": spec.name,
                    "ok": False,
                    "error_code": "PREPARE_FAILED",
                }
            )
            return {"draft": REFUND_FAILED, "pending_confirmation": None}
        emit({"type": "tool_result", "name": spec.name, "ok": True, "error_code": None})
        preview = prepared.preview
        if not preview["eligible"] or prepared.token is None or prepared.hash is None:
            # Lost a race against the policy: treat it as ineligible.
            return {
                "eligibility": {**(state.get("eligibility") or {}), **preview},
                "pending_confirmation": None,
                "draft": "",
            }
        card = {
            "kind": "refund_confirmation",
            "interrupt_id": str(uuid.uuid4()),
            "purchase": {
                "id": str(preview["purchase_id"]),
                "course": prepared.purchase_title,
            },
            "amount": preview["amount"],
            "currency": preview["currency"],
            "refund_method": preview["refund_method"],
            "reason": {
                "category": state["reason_category"],
                "text": state["reason_text"],
            },
            "window_deadline": prepared.window_deadline.isoformat()
            if prepared.window_deadline
            else None,
            "policy_citations": preview["citations"],
            "hash": prepared.hash,
            "expires_at": preview["expires_at"],
        }
        return {
            "pending_confirmation": card,
            "confirmation_token": prepared.token,
            "confirmation_hash": prepared.hash,
            "eligibility": {**(state.get("eligibility") or {}), **preview},
            "draft": "",
        }

    return prepare_confirmation


def route_prepare(state: SupportState) -> str:
    if state.get("pending_confirmation"):
        return "request_confirmation"
    if state.get("draft"):
        return "guard_output"
    return "explain_ineligible"

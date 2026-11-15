"""`collect_refund_info`: resolve the purchase and the reason (BR-12) or ask for them.

The model only *extracts* what the customer said (structured output). The candidate
purchases come from the read tool 2, and the chosen id is verified against them and
through tool 3 (ownership), so a made-up id never advances. Nothing is written here.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from pydantic import BaseModel

from app.agent.deps import AgentDeps
from app.agent.prompts import (
    ASK_PURCHASE,
    ASK_REASON,
    COLLECT_SYSTEM,
    NO_PURCHASES,
    REASON_INVALID,
)
from app.agent.state import SupportState
from app.agent.tools.base import run_tool
from app.agent.tools.registry import ALL_TOOLS
from app.domain.reason import ReasonRequired, validate_reason

MAX_CONTEXT_MESSAGES = 6


class CollectResult(BaseModel):
    purchase_id: str | None = None
    reason_category: str | None = None
    reason_text: str | None = None
    question: str | None = None


def _recent_customer_text(state: SupportState) -> str:
    texts = [
        str(m.content) for m in state.get("messages", []) if isinstance(m, HumanMessage)
    ]
    return "\n---\n".join(texts[-MAX_CONTEXT_MESSAGES:])


def _purchase_options(candidates: list[dict[str, Any]]) -> str:
    return "\n".join(f"- {c['course']} ({c['id']})" for c in candidates)


def make_collect_refund_info(
    deps: AgentDeps,
) -> Callable[[SupportState, RunnableConfig], Awaitable[dict[str, Any]]]:
    async def collect_refund_info(
        state: SupportState, config: RunnableConfig
    ) -> dict[str, Any]:
        ctx = deps.tool_context(config)
        emit = get_stream_writer()

        selected = state.get("selected_purchase_id")
        category = state.get("reason_category")
        text = state.get("reason_text")
        question: str | None = None

        # An edited reason arrives already validated by the HITL card: no extraction.
        if state.get("reason_edited"):
            extracted = CollectResult()
        else:
            args: dict[str, Any] = {"within_refund_window": None}
            emit({"type": "tool_call", "name": "list_my_purchases", "args": args})
            listing = await run_tool(ALL_TOOLS["list_my_purchases"], ctx, args)
            emit(
                {
                    "type": "tool_result",
                    "name": "list_my_purchases",
                    "ok": listing.ok,
                    "error_code": listing.error.code if listing.error else None,
                }
            )
            if not listing.ok:
                raise ConnectionError("list_my_purchases failed")  # retried by policy
            candidates = [
                {"id": str(p["id"]), "course": p["course"]}
                for p in listing.data["purchases"]
            ]
            if not candidates:
                return {"draft": NO_PURCHASES, "awaiting": None}
            known = {
                "purchase_id": selected,
                "reason_category": category,
                "reason_text": text,
            }
            prompt = [
                SystemMessage(COLLECT_SYSTEM),
                HumanMessage(
                    "<purchases>\n"
                    f"{json.dumps(candidates, ensure_ascii=False)}\n</purchases>\n"
                    "<known>\n"
                    f"{json.dumps(known, ensure_ascii=False)}\n"
                    "</known>\n<customer_messages>\n"
                    f"{_recent_customer_text(state)}\n</customer_messages>"
                ),
            ]
            raw = await deps.timed(
                deps.llm.with_structured_output(CollectResult).ainvoke(prompt)
            )
            extracted = (
                raw
                if isinstance(raw, CollectResult)
                else CollectResult.model_validate(raw)
            )
            valid_ids = {c["id"] for c in candidates}
            chosen = extracted.purchase_id or selected
            selected = chosen if chosen in valid_ids else None
            if selected is None and len(candidates) == 1:
                selected = candidates[0]["id"]
            category = extracted.reason_category or category
            text = extracted.reason_text or text
            question = extracted.question
            if selected is None:
                ask = question or ASK_PURCHASE
                return {
                    "draft": f"{ask}\n{_purchase_options(candidates)}",
                    "awaiting": "purchase",
                    "purchase_candidates": candidates,
                    "selected_purchase_id": None,
                    "reason_category": category,
                    "reason_text": text,
                }

        # Ownership/existence check through the read tool 3.
        details = await run_tool(
            ALL_TOOLS["get_purchase_details"], ctx, {"purchase_id": selected}
        )
        if not details.ok:
            return {
                "draft": ASK_PURCHASE,
                "awaiting": "purchase",
                "selected_purchase_id": None,
            }

        # BR-12: no advance without a valid reason.
        if not category or not text:
            return {
                "draft": question or ASK_REASON,
                "awaiting": "reason",
                "selected_purchase_id": selected,
                "reason_category": category,
                "reason_text": text,
                "reason_edited": False,
            }
        try:
            parsed, cleaned = validate_reason(category, text)
        except ReasonRequired:
            return {
                "draft": REASON_INVALID,
                "awaiting": "reason",
                "selected_purchase_id": selected,
                "reason_category": category,
                "reason_text": None,
                "reason_edited": False,
            }
        return {
            "awaiting": None,
            "draft": "",
            "selected_purchase_id": selected,
            "reason_category": str(parsed),
            "reason_text": cleaned,
            "reason_edited": False,
            "pending_confirmation": None,
            "confirmation_token": None,
            "confirmation_hash": None,
        }

    return collect_refund_info


def route_collect(state: SupportState) -> str:
    return (
        "guard_output"
        if state.get("awaiting") or state.get("draft")
        else "check_eligibility"
    )

"""`SupportState` (challenge §6). No `customer_id`, `role` or `request_id`."""

import enum
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

TOOL_BUDGET = 6


class Intent(enum.StrEnum):
    POLICY_QUESTION = "policy_question"
    REFUND_REQUEST = "refund_request"
    REFUND_STATUS = "refund_status"
    CANCEL_REFUND = "cancel_refund"
    OUT_OF_SCOPE = "out_of_scope"
    HUMAN_HANDOFF = "human_handoff"


class SupportState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    intent: str | None
    awaiting: Literal["purchase", "reason"] | None
    # Plain dicts so the checkpoint stays serializable: {"doc_key", "section_path"}.
    citations: list[dict[str, str]]
    guardrail_flags: list[str]
    tool_budget: int
    retries: int
    # Answer being built this turn; only `respond` turns it into a message,
    # after `guard_output` has filtered it.
    draft: str
    blocked: bool
    needs_handoff: bool
    handoff_reason: str


def turn_reset() -> dict[str, Any]:
    """Per-turn fields, cleared when a new customer message enters the graph."""
    return {
        "draft": "",
        "citations": [],
        "guardrail_flags": [],
        "tool_budget": TOOL_BUDGET,
        "retries": 0,
        "blocked": False,
        "needs_handoff": False,
        "handoff_reason": "",
        "intent": None,
    }

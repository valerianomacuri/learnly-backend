"""Explicit allowlist of tools (challenge §2 rule 2, §7).

There is no tool to approve, pay, read internal notes or take `customer_id`/`role`
as an argument; `tests/agent/test_tool_registry.py` enforces it.
"""

from app.agent.tools import (
    eligibility,
    escalate,
    policy,
    purchases,
    refunds,
    refunds_write,
)
from app.agent.tools.base import ToolSpec

ALL_TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        policy.SPEC,
        purchases.LIST_SPEC,
        purchases.DETAILS_SPEC,
        eligibility.ELIGIBILITY_SPEC,
        eligibility.PREPARE_SPEC,
        refunds_write.CREATE_SPEC,
        refunds.LIST_SPEC,
        refunds.STATUS_SPEC,
        refunds_write.CANCEL_SPEC,
        escalate.SPEC,
    )
}

# Read tools the `refund_status` ReAct sub-agent may call. Tools 4/5 (eligibility,
# confirmation) and the write tools 6/9 are invoked by deterministic nodes only and
# are never bound to a model.
LLM_BOUND_TOOLS: tuple[str, ...] = (
    "list_my_purchases",
    "get_purchase_details",
    "list_my_refunds",
    "get_refund_status",
)

FORBIDDEN_PARAMS = frozenset({"customer_id", "role", "user_id"})
FORBIDDEN_TOOL_WORDS = ("approve", "reject", "decide", "pay", "payout", "note")


def bound_specs() -> list[ToolSpec]:
    return [ALL_TOOLS[name] for name in LLM_BOUND_TOOLS]

"""Read-only support graph (spec 09, challenge §6).

START -> entry_router -> guard_input -> classify_intent -> {policy_qa | refund_status |
collect_refund_info | cancel_refund_flow | handoff} -> guard_output -> respond -> END.

Refund flow (spec 10): collect_refund_info -> check_eligibility -> {explain_ineligible |
prepare_confirmation -> request_confirmation (interrupt) -> create_refund}. The only
inbound edge of `create_refund` is `request_confirmation` (decision `confirm`), and
the same holds for `cancel_refund_apply`: no write is reachable without a human yes.
"""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy
from sqlalchemy.exc import OperationalError

from app.agent.deps import AgentDeps
from app.agent.nodes.cancel_refund import (
    make_cancel_refund_apply,
    make_cancel_refund_flow,
    request_cancel_confirmation,
    route_cancel_confirmation,
    route_cancel_flow,
)
from app.agent.nodes.check_eligibility import make_check_eligibility, route_eligibility
from app.agent.nodes.classify_intent import make_classify_intent, route_intent
from app.agent.nodes.collect_refund_info import (
    make_collect_refund_info,
    route_collect,
)
from app.agent.nodes.create_refund import make_create_refund, route_created
from app.agent.nodes.entry_router import entry_router, route_entry
from app.agent.nodes.explain_ineligible import make_explain_ineligible
from app.agent.nodes.guard_input import guard_input, route_guard
from app.agent.nodes.guard_output import guard_output
from app.agent.nodes.handoff import make_handoff
from app.agent.nodes.policy_qa import make_policy_qa, route_policy
from app.agent.nodes.prepare_confirmation import (
    make_prepare_confirmation,
    route_prepare,
)
from app.agent.nodes.refund_status import make_refund_status
from app.agent.nodes.request_confirmation import (
    make_request_confirmation,
    route_confirmation,
)
from app.agent.nodes.respond import respond
from app.agent.state import SupportState

RECURSION_LIMIT = 25


def is_transient(exc: Exception) -> bool:
    """Errors worth a retry with backoff: timeouts, network and rate limits."""
    if isinstance(exc, TimeoutError | ConnectionError | OperationalError):
        return True
    try:
        import openai
    except ImportError:  # pragma: no cover
        return False
    return isinstance(
        exc,
        openai.APIConnectionError | openai.APITimeoutError | openai.RateLimitError,
    )


RETRY = RetryPolicy(
    max_attempts=3, initial_interval=0.5, backoff_factor=2.0, retry_on=is_transient
)


def _n(node: Any) -> Any:
    """Nodes take `(state, config)`; LangGraph's overloads do not see that."""
    return node


def build_graph(
    deps: AgentDeps, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> CompiledStateGraph[Any, Any, Any, Any]:
    graph = StateGraph(SupportState)
    graph.add_node("entry_router", entry_router)
    graph.add_node("guard_input", guard_input)
    graph.add_node("classify_intent", _n(make_classify_intent(deps)))
    graph.add_node("policy_qa", _n(make_policy_qa(deps)), retry_policy=RETRY)
    graph.add_node("refund_status", _n(make_refund_status(deps)), retry_policy=RETRY)
    graph.add_node(
        "collect_refund_info", _n(make_collect_refund_info(deps)), retry_policy=RETRY
    )
    graph.add_node(
        "check_eligibility", _n(make_check_eligibility(deps)), retry_policy=RETRY
    )
    graph.add_node(
        "explain_ineligible", _n(make_explain_ineligible(deps)), retry_policy=RETRY
    )
    graph.add_node(
        "prepare_confirmation", _n(make_prepare_confirmation(deps)), retry_policy=RETRY
    )
    graph.add_node("request_confirmation", _n(make_request_confirmation(deps)))
    # Idempotent through the Idempotency-Key, so retrying it cannot duplicate.
    graph.add_node("create_refund", _n(make_create_refund(deps)), retry_policy=RETRY)
    graph.add_node(
        "cancel_refund_flow", _n(make_cancel_refund_flow(deps)), retry_policy=RETRY
    )
    graph.add_node("request_cancel_confirmation", request_cancel_confirmation)
    graph.add_node("cancel_refund_apply", _n(make_cancel_refund_apply(deps)))
    graph.add_node("handoff", _n(make_handoff(deps)))
    graph.add_node("guard_output", guard_output)
    graph.add_node("respond", respond)

    graph.add_edge(START, "entry_router")
    graph.add_conditional_edges("entry_router", route_entry, ["guard_input"])
    graph.add_conditional_edges(
        "guard_input",
        route_guard,
        ["classify_intent", "respond", "collect_refund_info", "cancel_refund_flow"],
    )
    graph.add_conditional_edges(
        "classify_intent",
        route_intent,
        [
            "policy_qa",
            "refund_status",
            "collect_refund_info",
            "cancel_refund_flow",
            "handoff",
        ],
    )
    graph.add_conditional_edges("policy_qa", route_policy, ["handoff", "guard_output"])
    for node in ("refund_status", "handoff", "explain_ineligible"):
        graph.add_edge(node, "guard_output")
    graph.add_conditional_edges(
        "collect_refund_info", route_collect, ["guard_output", "check_eligibility"]
    )
    graph.add_conditional_edges(
        "check_eligibility",
        route_eligibility,
        ["guard_output", "prepare_confirmation", "explain_ineligible"],
    )
    graph.add_conditional_edges(
        "prepare_confirmation",
        route_prepare,
        ["request_confirmation", "guard_output", "explain_ineligible"],
    )
    graph.add_conditional_edges(
        "request_confirmation",
        route_confirmation,
        ["create_refund", "collect_refund_info", "check_eligibility", "guard_output"],
    )
    graph.add_conditional_edges(
        "create_refund",
        route_created,
        ["explain_ineligible", "check_eligibility", "guard_output"],
    )
    graph.add_conditional_edges(
        "cancel_refund_flow",
        route_cancel_flow,
        ["request_cancel_confirmation", "guard_output"],
    )
    graph.add_conditional_edges(
        "request_cancel_confirmation",
        route_cancel_confirmation,
        ["cancel_refund_apply", "guard_output"],
    )
    graph.add_edge("cancel_refund_apply", "guard_output")
    graph.add_edge("guard_output", "respond")
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)

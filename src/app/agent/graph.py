"""Read-only support graph (spec 09, challenge §6).

START -> entry_router -> guard_input -> classify_intent -> {policy_qa | refund_status |
refund_form_notice | handoff} -> guard_output -> respond -> END. Spec 10 adds the
refund flow and the HITL nodes.
"""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RetryPolicy
from sqlalchemy.exc import OperationalError

from app.agent.deps import AgentDeps
from app.agent.nodes.classify_intent import make_classify_intent, route_intent
from app.agent.nodes.entry_router import entry_router, route_entry
from app.agent.nodes.guard_input import guard_input, route_guard
from app.agent.nodes.guard_output import guard_output
from app.agent.nodes.handoff import make_handoff
from app.agent.nodes.policy_qa import make_policy_qa, route_policy
from app.agent.nodes.refund_form_notice import refund_form_notice
from app.agent.nodes.refund_status import make_refund_status
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


def build_graph(
    deps: AgentDeps, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> CompiledStateGraph[Any, Any, Any, Any]:
    graph = StateGraph(SupportState)
    graph.add_node("entry_router", entry_router)
    graph.add_node("guard_input", guard_input)
    graph.add_node("classify_intent", make_classify_intent(deps))  # type: ignore[arg-type]
    graph.add_node("policy_qa", make_policy_qa(deps), retry_policy=RETRY)  # type: ignore[arg-type]
    graph.add_node("refund_status", make_refund_status(deps), retry_policy=RETRY)  # type: ignore[arg-type]
    graph.add_node("refund_form_notice", refund_form_notice)
    graph.add_node("handoff", make_handoff(deps))  # type: ignore[arg-type]
    graph.add_node("guard_output", guard_output)
    graph.add_node("respond", respond)

    graph.add_edge(START, "entry_router")
    graph.add_conditional_edges(
        "entry_router", route_entry, ["guard_input", "classify_intent"]
    )
    graph.add_conditional_edges(
        "guard_input", route_guard, ["classify_intent", "respond"]
    )
    graph.add_conditional_edges(
        "classify_intent",
        route_intent,
        ["policy_qa", "refund_status", "refund_form_notice", "handoff"],
    )
    graph.add_conditional_edges("policy_qa", route_policy, ["handoff", "guard_output"])
    for node in ("refund_status", "refund_form_notice", "handoff"):
        graph.add_edge(node, "guard_output")
    graph.add_edge("guard_output", "respond")
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)

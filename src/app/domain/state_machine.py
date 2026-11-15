"""Refund state machine (pure): which transitions exist and who may trigger them."""

from collections.abc import Mapping

from app.domain.enums import ActorType, RefundStatus
from app.domain.errors import DomainProblem

_S = RefundStatus
_A = ActorType

# (current, target) -> actors allowed to perform it.
TRANSITIONS: Mapping[tuple[RefundStatus, RefundStatus], frozenset[ActorType]] = {
    (_S.PENDING_APPROVAL, _S.APPROVED): frozenset({_A.SUPERVISOR}),
    (_S.PENDING_APPROVAL, _S.REJECTED): frozenset({_A.SUPERVISOR}),
    (_S.PENDING_APPROVAL, _S.CANCELLED): frozenset({_A.CUSTOMER, _A.AGENT}),
    (_S.APPROVED, _S.PROCESSING): frozenset({_A.SYSTEM}),
    (_S.PROCESSING, _S.COMPLETED): frozenset({_A.SYSTEM}),
    (_S.PROCESSING, _S.FAILED): frozenset({_A.SYSTEM}),
    (_S.FAILED, _S.PROCESSING): frozenset({_A.SYSTEM, _A.SUPERVISOR}),
}


class InvalidTransition(DomainProblem):
    code = "INVALID_TRANSITION"
    status = 409
    default_detail = "The refund cannot move to that state"


def transition(
    current: RefundStatus, target: RefundStatus, actor_role: ActorType
) -> RefundStatus:
    """Return `target` if the move is allowed, otherwise raise `InvalidTransition`."""
    actors = TRANSITIONS.get((current, target))
    if actors is None:
        raise InvalidTransition(f"Cannot move from {current} to {target}")
    if actor_role not in actors:
        raise InvalidTransition(f"{actor_role} cannot move from {current} to {target}")
    return target

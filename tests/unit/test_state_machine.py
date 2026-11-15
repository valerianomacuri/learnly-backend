import itertools

import pytest

from app.domain.enums import ActorType, RefundStatus
from app.domain.state_machine import TRANSITIONS, InvalidTransition, transition

S = RefundStatus
ALLOWED = {
    (S.PENDING_APPROVAL, S.APPROVED),
    (S.PENDING_APPROVAL, S.REJECTED),
    (S.PENDING_APPROVAL, S.CANCELLED),
    (S.APPROVED, S.PROCESSING),
    (S.PROCESSING, S.COMPLETED),
    (S.PROCESSING, S.FAILED),
    (S.FAILED, S.PROCESSING),
}


def test_matrix_is_exactly_the_spec() -> None:
    assert set(TRANSITIONS) == ALLOWED


@pytest.mark.parametrize(
    ("current", "target"), list(itertools.product(RefundStatus, RefundStatus))
)
def test_every_pair_of_the_7x7_matrix(current: S, target: S) -> None:
    allowed = (current, target) in ALLOWED
    if not allowed:
        with pytest.raises(InvalidTransition):
            transition(current, target, ActorType.SYSTEM)
        return
    actor = next(iter(TRANSITIONS[(current, target)]))
    assert transition(current, target, actor) == target


def test_actor_must_be_allowed() -> None:
    with pytest.raises(InvalidTransition):
        transition(S.PENDING_APPROVAL, S.APPROVED, ActorType.CUSTOMER)
    with pytest.raises(InvalidTransition):
        transition(S.PENDING_APPROVAL, S.CANCELLED, ActorType.SUPERVISOR)

"""RefundPolicyEngine: the 13 seed cases (challenge §9) and the boundaries."""

import uuid
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.cli.seed import CASES, CaseSpec
from app.domain.eligibility import (
    EligibilityFacts,
    PurchaseFacts,
    RefundPolicyEngine,
    refund_method_for,
)
from app.domain.enums import (
    EligibilityWarning,
    PaymentMethod,
    PurchaseStatus,
    PurchaseType,
    ReasonCode,
    RefundMethod,
    RefundStatus,
)
from app.domain.policy_config import PolicyConfig, PolicyConfigError

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
WINDOW = timedelta(days=30)
ORIGINAL = RefundMethod.ORIGINAL_PAYMENT
CREDITS = RefundMethod.PLATFORM_CREDITS
USER = uuid.UUID(int=1)
OTHER = uuid.UUID(int=2)

CONFIG = PolicyConfig(
    refund_window_days=30,
    max_progress_pct=30,
    abuse_flag_12m=3,
    abuse_block_12m=5,
    confirmation_ttl_min=10,
    sla_hours=48,
    one_active_request_per_purchase=True,
    rejected_is_final=True,
    policy_version="2026-10-01",
    reason_code_map={},
)


def engine(now: datetime = NOW, config: PolicyConfig = CONFIG) -> RefundPolicyEngine:
    return RefundPolicyEngine(config, lambda: now)


def facts(
    *,
    age: timedelta = timedelta(days=5),
    progress: int = 0,
    certificate: bool = False,
    status: PurchaseStatus = PurchaseStatus.ACTIVE,
    purchase_type: PurchaseType = PurchaseType.INDIVIDUAL,
    payment_method: PaymentMethod = PaymentMethod.CARD,
    existing: tuple[RefundStatus, ...] = (),
    completed_12m: int = 0,
    owner: uuid.UUID = USER,
    asker: uuid.UUID = USER,
) -> EligibilityFacts:
    return EligibilityFacts(
        requesting_user_id=asker,
        purchase=PurchaseFacts(
            customer_id=owner,
            status=status,
            purchase_type=purchase_type,
            payment_method=payment_method,
            purchased_at=NOW - age,
            amount=Decimal("49.99"),
            currency="USD",
        ),
        progress_pct=progress,
        certificate_issued_at=NOW - age if certificate else None,
        existing_requests=existing,
        completed_refunds_12m=completed_12m,
    )


def facts_for_case(case: CaseSpec) -> EligibilityFacts:
    existing: tuple[RefundStatus, ...] = ()
    if case.pending_refund:
        existing = (RefundStatus.PENDING_APPROVAL,)
    elif case.status is PurchaseStatus.REFUNDED:
        existing = (RefundStatus.COMPLETED,)
    return facts(
        age=case.age,
        progress=case.progress,
        certificate=case.certificate,
        status=case.status,
        purchase_type=case.purchase_type,
        payment_method=case.payment_method,
        existing=existing,
        completed_12m=case.past_refunds,
        owner=USER if case.owner == case.asker else OTHER,
        asker=USER,
    )


@pytest.mark.parametrize("case", CASES, ids=[c.key for c in CASES])
def test_seed_cases(case: CaseSpec) -> None:
    result = engine().evaluate(facts_for_case(case))
    assert result.eligible is case.eligible
    assert [c.value for c in result.reason_codes] == list(case.reason_codes)
    assert result.refund_method == case.refund_method
    assert [w.value for w in result.warnings] == list(case.warnings)
    assert result.policy_version == "2026-10-01"
    assert result.refund_window_days == 30


# --- BR-02: window, inclusive, UTC -------------------------------------------------


@pytest.mark.parametrize(
    ("age", "eligible"),
    [
        (WINDOW - timedelta(seconds=1), True),
        (WINDOW, True),
        (WINDOW + timedelta(seconds=1), False),
    ],
)
def test_window_boundary(age: timedelta, eligible: bool) -> None:
    result = engine().evaluate(facts(age=age))
    assert result.eligible is eligible
    expected = [] if eligible else [ReasonCode.OUTSIDE_REFUND_WINDOW]
    assert result.reason_codes == expected


@given(seconds=st.integers(min_value=-86_400, max_value=60 * 86_400))
def test_window_is_inclusive_for_any_age(seconds: int) -> None:
    result = engine().evaluate(facts(age=timedelta(seconds=seconds)))
    inside = seconds <= int(WINDOW.total_seconds())
    assert (ReasonCode.OUTSIDE_REFUND_WINDOW not in result.reason_codes) is inside
    assert result.days_since_purchase >= 0


def test_window_follows_the_config() -> None:
    config = CONFIG.model_copy(update={"refund_window_days": 14})
    assert engine(config=config).evaluate(
        facts(age=timedelta(days=15))
    ).reason_codes == [ReasonCode.OUTSIDE_REFUND_WINDOW]
    assert engine(config=config).evaluate(facts(age=timedelta(days=14))).eligible


def purchase_at(moment: datetime) -> EligibilityFacts:
    return EligibilityFacts(
        requesting_user_id=USER,
        purchase=PurchaseFacts(
            customer_id=USER,
            status=PurchaseStatus.ACTIVE,
            purchase_type=PurchaseType.INDIVIDUAL,
            payment_method=PaymentMethod.CARD,
            purchased_at=moment,
            amount=Decimal("1"),
            currency="USD",
        ),
    )


def test_non_utc_aware_datetimes_are_compared_as_instants() -> None:
    bought = (NOW - WINDOW).astimezone(timezone(timedelta(hours=5)))
    assert engine().evaluate(purchase_at(bought)).eligible  # exactly 30 days


def test_naive_purchase_datetime_is_an_error() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        engine().evaluate(purchase_at(datetime(2026, 11, 10)))


def test_naive_clock_is_an_error() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        engine(now=datetime(2026, 11, 15)).evaluate(facts())


# --- BR-06 / BR-10 boundaries ------------------------------------------------------


@pytest.mark.parametrize(("progress", "ok"), [(30, True), (31, False)])
def test_consumption_boundary(progress: int, ok: bool) -> None:
    result = engine().evaluate(facts(progress=progress))
    assert result.eligible is ok
    if not ok:
        assert result.reason_codes == [ReasonCode.CONSUMPTION_LIMIT_EXCEEDED]


@pytest.mark.parametrize(
    ("completed", "blocked", "flagged"),
    [(2, False, False), (3, False, True), (4, False, True), (5, True, False)],
)
def test_abuse_thresholds(completed: int, blocked: bool, flagged: bool) -> None:
    result = engine().evaluate(facts(completed_12m=completed))
    assert (ReasonCode.REFUND_ABUSE_LIMIT in result.reason_codes) is blocked
    assert (EligibilityWarning.HIGH_REFUND_FREQUENCY in result.warnings) is flagged
    assert result.eligible is (not blocked)


@given(completed=st.integers(min_value=0, max_value=50))
def test_abuse_rules_hold_for_any_history(completed: int) -> None:
    result = engine().evaluate(facts(completed_12m=completed))
    assert (ReasonCode.REFUND_ABUSE_LIMIT in result.reason_codes) is (completed >= 5)
    assert (EligibilityWarning.HIGH_REFUND_FREQUENCY in result.warnings) is (
        3 <= completed < 5
    )


@given(progress=st.integers(min_value=0, max_value=100))
def test_consumption_rule_for_any_progress(progress: int) -> None:
    result = engine().evaluate(facts(progress=progress))
    assert (ReasonCode.CONSUMPTION_LIMIT_EXCEEDED in result.reason_codes) is (
        progress > 30
    )


# --- BR-04 / BR-05: existing requests ----------------------------------------------


@pytest.mark.parametrize(
    "status",
    [RefundStatus.PENDING_APPROVAL, RefundStatus.APPROVED, RefundStatus.PROCESSING],
)
def test_active_request_blocks(status: RefundStatus) -> None:
    result = engine().evaluate(facts(existing=(status,)))
    assert result.reason_codes == [ReasonCode.ACTIVE_REFUND_EXISTS]


def test_cancelled_request_does_not_block() -> None:
    assert engine().evaluate(facts(existing=(RefundStatus.CANCELLED,))).eligible


def test_rejected_request_is_final() -> None:
    result = engine().evaluate(facts(existing=(RefundStatus.REJECTED,)))
    assert result.reason_codes == [ReasonCode.PREVIOUSLY_REJECTED]


def test_rejected_can_be_reopened_when_the_policy_says_so() -> None:
    config = CONFIG.model_copy(update={"rejected_is_final": False})
    result = engine(config=config).evaluate(facts(existing=(RefundStatus.REJECTED,)))
    assert result.eligible


# --- BR-08 / BR-09 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ptype", "code"),
    [
        (PurchaseType.SUBSCRIPTION, ReasonCode.SUBSCRIPTION_NOT_ELIGIBLE),
        (PurchaseType.THIRD_PARTY, ReasonCode.THIRD_PARTY_PURCHASE),
    ],
)
def test_ineligible_purchase_types(ptype: PurchaseType, code: ReasonCode) -> None:
    assert engine().evaluate(facts(purchase_type=ptype)).reason_codes == [code]


@pytest.mark.parametrize(
    ("payment", "ptype", "method"),
    [
        (PaymentMethod.CARD, PurchaseType.INDIVIDUAL, RefundMethod.ORIGINAL_PAYMENT),
        (PaymentMethod.PAYPAL, PurchaseType.INDIVIDUAL, RefundMethod.ORIGINAL_PAYMENT),
        (PaymentMethod.CREDITS, PurchaseType.INDIVIDUAL, RefundMethod.PLATFORM_CREDITS),
        (
            PaymentMethod.APPLE_IAP,
            PurchaseType.INDIVIDUAL,
            RefundMethod.PLATFORM_CREDITS,
        ),
        (
            PaymentMethod.GOOGLE_IAP,
            PurchaseType.INDIVIDUAL,
            RefundMethod.PLATFORM_CREDITS,
        ),
        (PaymentMethod.CARD, PurchaseType.GIFT, RefundMethod.PLATFORM_CREDITS),
    ],
)
def test_refund_method(
    payment: PaymentMethod, ptype: PurchaseType, method: RefundMethod
) -> None:
    assert refund_method_for(payment, ptype) is method
    result = engine().evaluate(facts(payment_method=payment, purchase_type=ptype))
    assert result.refund_method is method


# --- accumulation, not found, result shape ------------------------------------------


def test_all_applicable_codes_accumulate_in_a_stable_order() -> None:
    result = engine().evaluate(
        facts(
            age=timedelta(days=40),
            progress=80,
            certificate=True,
            status=PurchaseStatus.REFUNDED,
            purchase_type=PurchaseType.SUBSCRIPTION,
            existing=(RefundStatus.PENDING_APPROVAL, RefundStatus.REJECTED),
            completed_12m=6,
        )
    )
    assert result.reason_codes == [
        ReasonCode.OUTSIDE_REFUND_WINDOW,
        ReasonCode.ALREADY_REFUNDED,
        ReasonCode.ACTIVE_REFUND_EXISTS,
        ReasonCode.PREVIOUSLY_REJECTED,
        ReasonCode.CONSUMPTION_LIMIT_EXCEEDED,
        ReasonCode.CERTIFICATE_ISSUED,
        ReasonCode.SUBSCRIPTION_NOT_ELIGIBLE,
        ReasonCode.REFUND_ABUSE_LIMIT,
    ]
    assert not result.eligible
    assert result.refund_method is None


def test_missing_purchase_short_circuits() -> None:
    result = engine().evaluate(
        EligibilityFacts(
            requesting_user_id=USER,
            purchase=None,
            progress_pct=90,
            certificate_issued_at=NOW,
            completed_refunds_12m=9,
        )
    )
    assert result.reason_codes == [ReasonCode.PURCHASE_NOT_FOUND]
    assert result.warnings == []
    assert result.amount is None and result.refund_method is None


def test_foreign_purchase_looks_like_a_missing_one() -> None:
    foreign = engine().evaluate(facts(owner=OTHER, asker=USER))
    missing = engine().evaluate(
        EligibilityFacts(requesting_user_id=USER, purchase=None)
    )
    assert foreign == missing


def test_result_carries_the_clock_and_the_amount() -> None:
    result = engine().evaluate(facts(age=timedelta(days=12, hours=3)))
    assert result.checked_at == NOW
    assert result.days_since_purchase == 12
    assert result.amount == Decimal("49.99")


# --- PolicyConfig ---------------------------------------------------------------------

ELIGIBILITY_PARAMS = {
    "refund_window_days": 30,
    "max_progress_pct": 30,
    "abuse_flag_12m": 3,
    "abuse_block_12m": 5,
    "one_active_request_per_purchase": True,
    "rejected_is_final": True,
}
PROCESS_PARAMS = {"confirmation_ttl_min": 10, "sla_hours": 48}


def docs(**overrides: object) -> list[tuple[str, str, dict[str, object]]]:
    params: dict[str, object] = {**ELIGIBILITY_PARAMS, "reason_code_map": {"A": "b"}}
    params.update(overrides)
    return [
        ("refund-eligibility", "2026-10-01", params),
        ("refund-process", "2026-10-01", dict(PROCESS_PARAMS)),
        ("refund-faq", "2026-10-01", {}),
    ]


def test_config_merges_the_params_of_every_document() -> None:
    config = PolicyConfig.from_documents(docs())
    assert config.refund_window_days == 30
    assert config.sla_hours == 48
    assert config.policy_version == "2026-10-01"
    assert config.reason_code_map == {"A": "b"}


@pytest.mark.parametrize("missing", sorted(ELIGIBILITY_PARAMS))
def test_a_missing_param_is_an_error(missing: str) -> None:
    documents = docs()
    del documents[0][2][missing]
    with pytest.raises(PolicyConfigError, match=missing):
        PolicyConfig.from_documents(documents)


def test_a_mistyped_param_is_an_error() -> None:
    with pytest.raises(PolicyConfigError, match="refund_window_days"):
        PolicyConfig.from_documents(docs(refund_window_days="thirty"))


@pytest.mark.parametrize(
    "bad",
    [{"max_progress_pct": 101}, {"abuse_flag_12m": 6}, {"refund_window_days": 0}],
)
def test_contradictory_params_are_an_error(bad: dict[str, int]) -> None:
    with pytest.raises(PolicyConfigError):
        PolicyConfig.from_documents(docs(**bad))


def test_conflicting_values_across_documents_are_an_error() -> None:
    documents = docs()
    documents[1][2]["sla_hours"] = 24
    documents[2][2]["sla_hours"] = 48
    with pytest.raises(PolicyConfigError, match="sla_hours"):
        PolicyConfig.from_documents(documents)


def test_the_eligibility_document_is_required() -> None:
    with pytest.raises(PolicyConfigError, match="refund-eligibility"):
        PolicyConfig.from_documents(docs()[1:])

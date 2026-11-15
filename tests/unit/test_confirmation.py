import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain import confirmation
from app.domain.confirmation import ConfirmationInvalid, ConfirmationRequired
from app.domain.reason import ReasonRequired, validate_reason

SECRET = "s" * 32
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
CUSTOMER = uuid.uuid4()
PURCHASE = uuid.uuid4()


def make_hash(**over: object) -> str:
    args: dict[str, object] = {
        "purchase_id": PURCHASE,
        "reason_category": "NOT_AS_DESCRIBED",
        "reason_text": "El curso promete proyectos reales y solo hay teoría.",
        "amount": Decimal("49.99"),
        "refund_method": "ORIGINAL_PAYMENT",
    }
    args.update(over)
    return confirmation.payload_hash(**args)  # type: ignore[arg-type]


def issue(**over: object) -> str:
    args: dict[str, object] = {
        "secret": SECRET,
        "customer_id": CUSTOMER,
        "hash": make_hash(),
        "now": NOW,
        "ttl_min": 10,
    }
    args.update(over)
    return confirmation.issue_token(**args)[0]  # type: ignore[arg-type]


def check(token: str | None, **over: object) -> None:
    args: dict[str, object] = {
        "secret": SECRET,
        "payload_hash": make_hash(),
        "customer_id": CUSTOMER,
        "now": NOW + timedelta(minutes=1),
    }
    args.update(over)
    confirmation.verify(token, **args)  # type: ignore[arg-type]


def test_valid_token_verifies() -> None:
    check(issue())


def test_missing_token_is_required() -> None:
    with pytest.raises(ConfirmationRequired):
        check(None)
    with pytest.raises(ConfirmationRequired):
        check("")


@pytest.mark.parametrize(
    "field",
    [
        {"purchase_id": uuid.uuid4()},
        {"reason_category": "OTHER"},
        {"reason_text": "El curso promete proyectos reales y solo hay teoria."},
        {"amount": Decimal("49.98")},
        {"refund_method": "PLATFORM_CREDITS"},
    ],
)
def test_changing_any_payload_field_invalidates(field: dict[str, object]) -> None:
    with pytest.raises(ConfirmationInvalid):
        check(issue(), payload_hash=make_hash(**field))


def test_expired_foreign_and_tampered_tokens_are_invalid() -> None:
    token = issue()
    with pytest.raises(ConfirmationInvalid):
        check(token, now=NOW + timedelta(minutes=10))
    with pytest.raises(ConfirmationInvalid):
        check(token, customer_id=uuid.uuid4())
    with pytest.raises(ConfirmationInvalid):
        check(token, secret="o" * 32)
    flipped = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(ConfirmationInvalid):
        check(flipped)
    with pytest.raises(ConfirmationInvalid):
        check("garbage")


@pytest.mark.parametrize(
    "text",
    ["corto", "x" * 1001, "palabra palabra palabra palabra palabra", "a b " * 3],
)
def test_reason_rules(text: str) -> None:
    with pytest.raises(ReasonRequired):
        validate_reason("NOT_AS_DESCRIBED", text)


def test_reason_category_and_ok() -> None:
    with pytest.raises(ReasonRequired):
        validate_reason("NOPE", "una razón perfectamente válida y larga")
    cat, text = validate_reason("OTHER", "  una razón perfectamente válida y larga ")
    assert cat == "OTHER"
    assert text == "una razón perfectamente válida y larga"

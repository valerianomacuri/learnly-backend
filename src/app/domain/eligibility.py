"""Deterministic refund eligibility (BR-01 … BR-10). Pure: no database, no wall clock.

The application service gathers the facts; the engine only decides. The LLM never
does (D1). All applicable reason codes are accumulated, in a stable order.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.domain.enums import (
    ACTIVE_REFUND_STATUSES,
    EligibilityWarning,
    PaymentMethod,
    PurchaseStatus,
    PurchaseType,
    ReasonCode,
    RefundMethod,
    RefundStatus,
)
from app.domain.policy_config import PolicyConfig

_ORIGINAL_PAYMENT_METHODS = frozenset({PaymentMethod.CARD, PaymentMethod.PAYPAL})


@dataclass(frozen=True, slots=True)
class PurchaseFacts:
    customer_id: uuid.UUID
    status: PurchaseStatus
    purchase_type: PurchaseType
    payment_method: PaymentMethod
    purchased_at: datetime
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class EligibilityFacts:
    """Everything the engine needs. `purchase` is None when it does not exist."""

    requesting_user_id: uuid.UUID
    purchase: PurchaseFacts | None
    progress_pct: int = 0
    certificate_issued_at: datetime | None = None
    existing_requests: tuple[RefundStatus, ...] = ()
    completed_refunds_12m: int = 0


class EligibilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligible: bool
    reason_codes: list[ReasonCode] = []
    warnings: list[EligibilityWarning] = []  # supervisor only
    days_since_purchase: int
    refund_window_days: int
    progress_pct: int
    refund_method: RefundMethod | None
    amount: Decimal | None
    policy_version: str
    checked_at: datetime


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware (UTC)")


def refund_method_for(
    payment_method: PaymentMethod, purchase_type: PurchaseType
) -> RefundMethod:
    """BR-09: gifts and credit / in-app purchases come back as platform credits."""
    if purchase_type is PurchaseType.GIFT:
        return RefundMethod.PLATFORM_CREDITS
    if payment_method in _ORIGINAL_PAYMENT_METHODS:
        return RefundMethod.ORIGINAL_PAYMENT
    return RefundMethod.PLATFORM_CREDITS


class RefundPolicyEngine:
    def __init__(self, config: PolicyConfig, clock: Callable[[], datetime]) -> None:
        self.config = config
        self.clock = clock

    def evaluate(self, facts: EligibilityFacts) -> EligibilityResult:
        config = self.config
        now = self.clock()
        _require_aware(now, "clock()")
        purchase = facts.purchase

        # BR-01: someone else's purchase looks exactly like a missing one, and
        # nothing else is evaluated.
        if purchase is None or purchase.customer_id != facts.requesting_user_id:
            return EligibilityResult(
                eligible=False,
                reason_codes=[ReasonCode.PURCHASE_NOT_FOUND],
                days_since_purchase=0,
                refund_window_days=config.refund_window_days,
                progress_pct=0,
                refund_method=None,
                amount=None,
                policy_version=config.policy_version,
                checked_at=now,
            )
        _require_aware(purchase.purchased_at, "purchased_at")

        elapsed = now - purchase.purchased_at
        codes: list[ReasonCode] = []

        # BR-02: the limit is inclusive.
        if elapsed > timedelta(days=config.refund_window_days):
            codes.append(ReasonCode.OUTSIDE_REFUND_WINDOW)
        # BR-03
        if purchase.status is not PurchaseStatus.ACTIVE:
            codes.append(ReasonCode.ALREADY_REFUNDED)
        # BR-04
        if config.one_active_request_per_purchase and any(
            s in ACTIVE_REFUND_STATUSES for s in facts.existing_requests
        ):
            codes.append(ReasonCode.ACTIVE_REFUND_EXISTS)
        # BR-05: REJECTED blocks; CANCELLED does not.
        if (
            config.rejected_is_final
            and RefundStatus.REJECTED in facts.existing_requests
        ):
            codes.append(ReasonCode.PREVIOUSLY_REJECTED)
        # BR-06
        if facts.progress_pct > config.max_progress_pct:
            codes.append(ReasonCode.CONSUMPTION_LIMIT_EXCEEDED)
        # BR-07
        if facts.certificate_issued_at is not None:
            codes.append(ReasonCode.CERTIFICATE_ISSUED)
        # BR-08
        if purchase.purchase_type is PurchaseType.SUBSCRIPTION:
            codes.append(ReasonCode.SUBSCRIPTION_NOT_ELIGIBLE)
        elif purchase.purchase_type is PurchaseType.THIRD_PARTY:
            codes.append(ReasonCode.THIRD_PARTY_PURCHASE)
        # BR-10: blocks at the limit; the flag below it does not block.
        warnings: list[EligibilityWarning] = []
        if facts.completed_refunds_12m >= config.abuse_block_12m:
            codes.append(ReasonCode.REFUND_ABUSE_LIMIT)
        elif facts.completed_refunds_12m >= config.abuse_flag_12m:
            warnings.append(EligibilityWarning.HIGH_REFUND_FREQUENCY)

        eligible = not codes
        return EligibilityResult(
            eligible=eligible,
            reason_codes=codes,
            warnings=warnings,
            days_since_purchase=max(elapsed.days, 0),
            refund_window_days=config.refund_window_days,
            progress_pct=facts.progress_pct,
            # BR-09 fixes the method only for a refund that can happen.
            refund_method=(
                refund_method_for(purchase.payment_method, purchase.purchase_type)
                if eligible
                else None
            ),
            amount=purchase.amount,
            policy_version=config.policy_version,
            checked_at=now,
        )

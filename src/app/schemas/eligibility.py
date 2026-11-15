from datetime import datetime

from pydantic import BaseModel

from app.domain.eligibility import EligibilityResult
from app.domain.enums import ReasonCode, RefundMethod
from app.schemas.purchase import Money


class EligibilityRead(BaseModel):
    """Eligibility as the customer sees it.

    It deliberately has no `warnings`: flags such as HIGH_REFUND_FREQUENCY are for
    the supervisor only.
    """

    eligible: bool
    reason_codes: list[ReasonCode]
    days_since_purchase: int
    refund_window_days: int
    progress_pct: int
    refund_method: RefundMethod | None
    amount: Money | None
    policy_version: str
    checked_at: datetime

    @classmethod
    def from_result(cls, result: EligibilityResult) -> "EligibilityRead":
        return cls.model_validate(result.model_dump(exclude={"warnings"}))

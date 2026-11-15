import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.domain.enums import PaymentMethod, PurchaseStatus, PurchaseType

# Money is stored as NUMERIC and kept as Decimal in Python; JSON gets a number
# (`49.99`), as in the challenge contracts.
Money = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


class CourseSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    instructor_name: str


class ProgressRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    progress_pct: int
    lectures_completed: int
    last_accessed_at: datetime | None
    certificate_issued_at: datetime | None


class PurchaseRead(BaseModel):
    """A purchase as its owner sees it."""

    id: uuid.UUID
    course: CourseSummary
    amount: Money
    currency: str
    payment_method: PaymentMethod
    purchase_type: PurchaseType
    purchased_at: datetime
    status: PurchaseStatus
    refund_deadline: datetime
    within_refund_window: bool


class PurchaseDetail(PurchaseRead):
    progress: ProgressRead | None


class PurchasePage(BaseModel):
    items: list[PurchaseRead]
    next_cursor: str | None

"""Supervisor views of a refund. Deliberately separate from the customer schemas.

Nothing here reuses `RefundRead`: the supervisor sees the full eligibility
snapshot (with `warnings`), internal notes and event payloads, but only a masked
email of the customer. The customer schemas must never gain those fields.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    ActorType,
    Channel,
    EligibilityWarning,
    PaymentMethod,
    PurchaseStatus,
    PurchaseType,
    ReasonCategory,
    ReasonCode,
    RefundMethod,
    RefundStatus,
)
from app.schemas.base import RequestDTO
from app.schemas.purchase import CourseSummary, Money, ProgressRead
from app.schemas.refund import Citation

NoteBody = Annotated[str, Field(min_length=1, max_length=2000)]


class CustomerSummary(BaseModel):
    """Minimal personal data: the name and a masked email, nothing else."""

    id: uuid.UUID
    name: str
    email: str  # always masked (`l***@gmail.com`)


class SupervisorSnapshot(BaseModel):
    """The engine output frozen at creation, warnings included.

    Every field is optional: older or seeded rows carry a partial snapshot.
    """

    model_config = ConfigDict(extra="ignore")

    eligible: bool | None = None
    reason_codes: list[ReasonCode] = []
    warnings: list[EligibilityWarning] = []
    days_since_purchase: int | None = None
    refund_window_days: int | None = None
    progress_pct: int | None = None
    policy_version: str | None = None
    checked_at: datetime | None = None


class RefundFlags(BaseModel):
    sla_breached: bool
    high_risk: bool
    warnings: list[EligibilityWarning]


class SupervisorRefundRead(BaseModel):
    """A queue row: what the supervisor needs to pick and triage a request."""

    id: uuid.UUID
    purchase_id: uuid.UUID
    customer: CustomerSummary
    status: RefundStatus
    reason_category: ReasonCategory
    reason_text: str
    refund_method: RefundMethod
    amount: Money
    currency: str
    channel: Channel
    policy_version: str
    version: int
    created_at: datetime
    updated_at: datetime
    sla_due_at: datetime | None  # only while PENDING_APPROVAL
    flags: RefundFlags
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    decision_note: str | None


class SupervisorRefundPage(BaseModel):
    items: list[SupervisorRefundRead]
    next_cursor: str | None


class PurchaseReview(BaseModel):
    id: uuid.UUID
    course: CourseSummary
    amount: Money
    currency: str
    payment_method: PaymentMethod
    purchase_type: PurchaseType
    purchased_at: datetime
    status: PurchaseStatus
    progress: ProgressRead | None


class HistoryItem(BaseModel):
    id: uuid.UUID
    status: RefundStatus
    reason_category: ReasonCategory
    amount: Money
    created_at: datetime


class CustomerHistory(BaseModel):
    total: int
    by_status: dict[str, int]
    recent: list[HistoryItem]


class NoteCreate(RequestDTO):
    body: NoteBody


class NoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    refund_id: uuid.UUID
    supervisor_id: uuid.UUID
    body: str
    created_at: datetime


class SupervisorRefundDetail(SupervisorRefundRead):
    eligibility: SupervisorSnapshot
    purchase: PurchaseReview
    customer_history: CustomerHistory
    citations: list[Citation]
    notes: list[NoteRead]


class DecisionRequest(RequestDTO):
    note: Annotated[str | None, Field(max_length=2000)] = None


class SupervisorEventRead(BaseModel):
    """Full timeline entry, internal events and payload included."""

    type: str
    actor_type: ActorType
    actor_id: uuid.UUID | None
    payload: dict[str, Any]
    created_at: datetime


class ReasonCount(BaseModel):
    reason_category: ReasonCategory
    count: int


class SupervisorStats(BaseModel):
    pending: int
    sla_breached: int
    avg_decision_seconds: float | None
    approval_rate: float | None  # approved / decided; None until something is decided
    top_reasons: list[ReasonCount]

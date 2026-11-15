import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    ActorType,
    Channel,
    ReasonCategory,
    ReasonCode,
    RefundMethod,
    RefundStatus,
)
from app.schemas.base import RequestDTO
from app.schemas.purchase import Money

ReasonText = Annotated[str, Field(max_length=5000)]


class PreviewRequest(RequestDTO):
    purchase_id: uuid.UUID
    reason_category: ReasonCategory
    reason_text: ReasonText


class RefundCreate(PreviewRequest):
    # Absent is a domain error (CONFIRMATION_REQUIRED), not a validation error.
    confirmation_token: Annotated[str | None, Field(max_length=2000)] = None


class Citation(BaseModel):
    doc_key: str
    section_path: str


class PreviewResult(BaseModel):
    eligible: bool
    reason_codes: list[ReasonCode]
    purchase_id: uuid.UUID
    amount: Money | None
    currency: str | None
    refund_method: RefundMethod | None
    days_since_purchase: int
    refund_window_days: int
    progress_pct: int
    policy_version: str
    citations: list[Citation]
    confirmation_token: str | None
    expires_at: datetime | None


class EligibilitySnapshotRead(BaseModel):
    """The engine output frozen at creation, without the supervisor-only warnings."""

    eligible: bool
    reason_codes: list[ReasonCode]
    days_since_purchase: int
    refund_window_days: int
    progress_pct: int
    policy_version: str
    checked_at: datetime


class RefundRead(BaseModel):
    """A refund as its owner sees it: no internal notes, no `warnings`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    purchase_id: uuid.UUID
    status: RefundStatus
    reason_category: ReasonCategory
    reason_text: str
    refund_method: RefundMethod
    amount: Money
    currency: str
    channel: Channel
    policy_version: str
    eligibility_snapshot: EligibilitySnapshotRead
    confirmed_at: datetime | None
    decided_at: datetime | None
    decision_note: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class RefundPage(BaseModel):
    items: list[RefundRead]
    next_cursor: str | None


class RefundEventRead(BaseModel):
    """Public timeline entry: never the internal `payload`."""

    type: str
    actor_type: ActorType
    created_at: datetime

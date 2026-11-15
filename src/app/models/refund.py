import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    DDL,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import (
    ACTIVE_REFUND_STATUSES,
    ActorType,
    Channel,
    PayoutStatus,
    ReasonCategory,
    RefundMethod,
    RefundStatus,
)
from app.models.base import Base, IdMixin, TimestampMixin, str_enum

_ACTIVE_PREDICATE = "status IN ({})".format(
    ", ".join(f"'{status.value}'" for status in ACTIVE_REFUND_STATUSES)
)


class RefundRequest(IdMixin, TimestampMixin, Base):
    __tablename__ = "refund_requests"
    __table_args__ = (
        UniqueConstraint("customer_id", "idempotency_key"),
        CheckConstraint(
            "char_length(reason_text) BETWEEN 20 AND 1000", name="reason_text_length"
        ),
        # BR-04: one active request per purchase. A new one is allowed once the
        # previous one is CANCELLED (or otherwise out of these states).
        Index(
            "uq_refund_requests_active_purchase",
            "purchase_id",
            unique=True,
            postgresql_where=text(_ACTIVE_PREDICATE),
        ),
        Index("ix_refund_requests_customer_created", "customer_id", "created_at"),
        Index("ix_refund_requests_status_created", "status", "created_at"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    purchase_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("purchases.id"))
    status: Mapped[RefundStatus] = mapped_column(
        str_enum(RefundStatus, "refund_status"), default=RefundStatus.PENDING_APPROVAL
    )
    reason_category: Mapped[ReasonCategory] = mapped_column(
        str_enum(ReasonCategory, "reason_category")
    )
    reason_text: Mapped[str] = mapped_column(Text)
    refund_method: Mapped[RefundMethod] = mapped_column(
        str_enum(RefundMethod, "refund_method")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(CHAR(3))
    channel: Mapped[Channel] = mapped_column(str_enum(Channel, "channel"))
    chat_thread_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("chat_threads.id"), default=None
    )
    eligibility_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    policy_version: Mapped[str] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    confirmation_hash: Mapped[str | None] = mapped_column(Text, default=None)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), default=None
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    decision_note: Mapped[str | None] = mapped_column(Text, default=None)
    idempotency_key: Mapped[str] = mapped_column(Text)
    # Optimistic lock checked explicitly against `If-Match` (not SQLAlchemy's
    # `version_id_col`): the services bump it on every transition.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class RefundEvent(IdMixin, Base):
    """Append-only audit trail (the only one). UPDATE/DELETE are blocked by a trigger.

    `payload` is internal: it is never serialized in customer-facing schemas.
    """

    __tablename__ = "refund_events"
    __table_args__ = (
        Index("ix_refund_events_refund_created", "refund_id", "created_at"),
    )

    refund_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("refund_requests.id"))
    type: Mapped[str] = mapped_column(String(50))
    actor_type: Mapped[ActorType] = mapped_column(str_enum(ActorType, "actor_type"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    # clock_timestamp(), not now(): several events in one transaction must keep
    # their order in the timeline.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


APPEND_ONLY_TRIGGER = "trg_refund_events_append_only"
APPEND_ONLY_FUNCTION = "refund_events_append_only"
# Migration 0002 carries its own copy of this SQL (migrations never import models).
APPEND_ONLY_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {APPEND_ONLY_FUNCTION}() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'refund_events is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql
"""
APPEND_ONLY_TRIGGER_SQL = f"""
CREATE TRIGGER {APPEND_ONLY_TRIGGER}
BEFORE UPDATE OR DELETE ON refund_events
FOR EACH ROW EXECUTE FUNCTION {APPEND_ONLY_FUNCTION}()
"""

# `create_all` (used by the tests) must also get the trigger. The `%` signs are
# doubled because DDL statements are %-formatted.
for _sql in (APPEND_ONLY_FUNCTION_SQL, APPEND_ONLY_TRIGGER_SQL):
    event.listen(
        RefundEvent.__table__,
        "after_create",
        DDL(_sql.replace("%", "%%")).execute_if(dialect="postgresql"),  # type: ignore[no-untyped-call]
    )


class RefundInternalNote(IdMixin, Base):
    """Supervisor-only notes. Never serialized in customer schemas or LLM context."""

    __tablename__ = "refund_internal_notes"

    refund_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("refund_requests.id"), index=True
    )
    supervisor_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RefundPayout(IdMixin, TimestampMixin, Base):
    """Payment execution, separate from the approval (used from spec 05)."""

    __tablename__ = "refund_payouts"

    refund_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("refund_requests.id"), unique=True
    )
    method: Mapped[RefundMethod] = mapped_column(
        str_enum(RefundMethod, "payout_method")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    provider_ref: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[PayoutStatus] = mapped_column(
        str_enum(PayoutStatus, "payout_status"), default=PayoutStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)


class CreditLedgerEntry(IdMixin, Base):
    """Append-only by convention (no UPDATE/DELETE): balance = SUM(delta)."""

    __tablename__ = "credit_ledger"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), index=True
    )
    delta: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    refund_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("refund_requests.id"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

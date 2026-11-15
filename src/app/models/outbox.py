from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import OutboxStatus
from app.models.base import Base, IdMixin, TimestampMixin, str_enum


class OutboxEvent(IdMixin, TimestampMixin, Base):
    """Reliable delivery to the payout worker and notifications (used from spec 05)."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_events_status_available", "status", "available_at"),
    )

    topic: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[OutboxStatus] = mapped_column(
        str_enum(OutboxStatus, "outbox_status"), default=OutboxStatus.PENDING
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

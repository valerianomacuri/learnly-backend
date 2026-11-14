import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, str_enum


class AuditDecision(enum.StrEnum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"


class AuditEvent(IdMixin, Base):
    """Append-only record of sensitive actions. Never updated or deleted."""

    __tablename__ = "audit_events"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(50))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    decision: Mapped[AuditDecision] = mapped_column(
        str_enum(AuditDecision, "audit_decision")
    )
    reason: Mapped[str | None] = mapped_column(Text, default=None)

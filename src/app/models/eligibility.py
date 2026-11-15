import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin


class EligibilityCheck(IdMixin, Base):
    """Audit of eligibility queries and rejected attempts (D6).

    `purchase_id` has no foreign key: a check for a missing (or someone else's)
    purchase is recorded with `PURCHASE_NOT_FOUND` and must still be storable.
    """

    __tablename__ = "eligibility_checks"
    __table_args__ = (
        Index("ix_eligibility_checks_customer_created", "customer_id", "created_at"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    purchase_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source: Mapped[str] = mapped_column(String(30))
    eligible: Mapped[bool] = mapped_column(Boolean)
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    policy_version: Mapped[str] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

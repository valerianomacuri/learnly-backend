import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import PaymentMethod, PurchaseStatus, PurchaseType
from app.models.base import Base, IdMixin, TimestampMixin, str_enum
from app.models.course import Course


class Purchase(IdMixin, TimestampMixin, Base):
    __tablename__ = "purchases"
    __table_args__ = (
        # Serves the cursor pagination of `GET /purchases` (newest first).
        Index(
            "ix_purchases_customer_purchased_at", "customer_id", "purchased_at", "id"
        ),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    course_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("courses.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(CHAR(3))
    payment_method: Mapped[PaymentMethod] = mapped_column(
        str_enum(PaymentMethod, "payment_method")
    )
    purchase_type: Mapped[PurchaseType] = mapped_column(
        str_enum(PurchaseType, "purchase_type")
    )
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[PurchaseStatus] = mapped_column(
        str_enum(PurchaseStatus, "purchase_status"),
        default=PurchaseStatus.ACTIVE,
        server_default=PurchaseStatus.ACTIVE.value,
    )

    course: Mapped[Course] = relationship(lazy="raise")
    progress: Mapped["CourseProgress | None"] = relationship(
        back_populates="purchase", lazy="raise", uselist=False
    )


class CourseProgress(IdMixin, TimestampMixin, Base):
    __tablename__ = "course_progress"
    __table_args__ = (
        CheckConstraint("progress_pct BETWEEN 0 AND 100", name="progress_pct_range"),
    )

    purchase_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("purchases.id"), unique=True
    )
    progress_pct: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lectures_completed: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    certificate_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    purchase: Mapped[Purchase] = relationship(back_populates="progress", lazy="raise")

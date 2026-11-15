from datetime import date
from typing import Any

from sqlalchemy import Date, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import PolicyStatus
from app.models.base import Base, IdMixin, TimestampMixin, str_enum


class PolicyDocument(IdMixin, TimestampMixin, Base):
    """Versioned policy; `params` is the YAML front-matter (spec 02 reads it)."""

    __tablename__ = "policy_documents"
    __table_args__ = (
        UniqueConstraint("doc_key", "version"),
        # At most one ACTIVE version per document (atomic activation, spec 07).
        Index(
            "uq_policy_documents_active_doc_key",
            "doc_key",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    doc_key: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(50))
    effective_date: Mapped[date] = mapped_column(Date)
    checksum: Mapped[str] = mapped_column(Text)
    # Display copy for the policy viewer (no LLM); NULL until `policies-load` runs.
    title: Mapped[str | None] = mapped_column(String(200), default=None)
    content: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[PolicyStatus] = mapped_column(
        str_enum(PolicyStatus, "policy_status"), default=PolicyStatus.ACTIVE
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Embedding model the chunks of this version were built with (spec 07).
    embedding_model: Mapped[str | None] = mapped_column(String(100), default=None)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, default=None)

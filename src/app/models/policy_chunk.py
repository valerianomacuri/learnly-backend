import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.models.base import Base, IdMixin, TimestampMixin

EMBEDDING_DIM = get_settings().embedding_dim


class PolicyChunk(IdMixin, TimestampMixin, Base):
    """Retrievable fragment of a policy document version (spec 07)."""

    __tablename__ = "policy_chunks"
    __table_args__ = (
        Index(
            "ix_policy_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_policy_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_policy_chunks_document_id", "document_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("policy_documents.id", ondelete="CASCADE")
    )
    parent_section_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("policy_chunks.id", ondelete="SET NULL"), default=None
    )
    section_path: Mapped[str] = mapped_column(Text)
    heading_level: Mapped[int] = mapped_column(Integer)
    chunk_type: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    embedded_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('spanish', content)", persisted=True)
    )
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    token_count: Mapped[int] = mapped_column(Integer)
    source_lines: Mapped[str] = mapped_column(String(32), default="")
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict
    )

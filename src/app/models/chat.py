import uuid

from sqlalchemy import ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import ThreadStatus
from app.models.base import Base, IdMixin, TimestampMixin, str_enum


class ChatThread(IdMixin, TimestampMixin, Base):
    """Checkpoints live in LangGraph's checkpointer; this row only owns the thread."""

    __tablename__ = "chat_threads"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), index=True
    )
    status: Mapped[ThreadStatus] = mapped_column(
        str_enum(ThreadStatus, "thread_status"), default=ThreadStatus.ACTIVE
    )

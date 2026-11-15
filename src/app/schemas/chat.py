import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import ThreadStatus

MAX_MESSAGE_CHARS = 4000


class ThreadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: ThreadStatus
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ResumeRequest(BaseModel):
    """The customer's answer to the confirmation card (HITL)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    interrupt_id: str = Field(min_length=1, max_length=64)
    decision: Literal["confirm", "cancel", "edit"]
    edited_reason: str | None = Field(default=None, max_length=2000)


class ChatMessageRead(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    citations: list[dict[str, str]] = []
    # Set only on the trailing entry that carries the pending confirmation card.
    interrupt: dict[str, Any] | None = None

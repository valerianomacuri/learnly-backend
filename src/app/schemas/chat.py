import uuid
from datetime import datetime
from typing import Literal

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


class ChatMessageRead(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    citations: list[dict[str, str]] = []

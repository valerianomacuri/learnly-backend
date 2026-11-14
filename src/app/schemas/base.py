from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RequestDTO(BaseModel):
    """Base for request bodies: strict, rejects unknown fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReadDTO(BaseModel):
    """Base for responses built from ORM objects."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    updated_at: datetime

from datetime import date

from pydantic import BaseModel, ConfigDict


class PolicySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    doc_key: str
    title: str | None
    version: str
    effective_date: date


class PolicyRead(PolicySummary):
    content: str  # Markdown

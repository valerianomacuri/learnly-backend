import uuid

from pydantic import BaseModel

from app.schemas.purchase import Money


class MeRead(BaseModel):
    """Profile, roles and credit balance (SUM of the credit ledger) of the caller."""

    id: uuid.UUID
    email: str
    name: str
    roles: list[str]
    credit_balance: Money

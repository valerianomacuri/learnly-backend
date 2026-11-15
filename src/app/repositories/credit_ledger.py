import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.models.refund import CreditLedgerEntry
from app.repositories.base import BaseRepository


class CreditLedgerRepository(BaseRepository[CreditLedgerEntry]):
    model = CreditLedgerEntry

    async def balance(self, customer_id: uuid.UUID) -> Decimal:
        """Credit balance = SUM(delta) over the append-only ledger."""
        total = await self.session.scalar(
            select(func.coalesce(func.sum(CreditLedgerEntry.delta), 0)).where(
                CreditLedgerEntry.customer_id == customer_id
            )
        )
        return Decimal(total or 0)

    async def get_for_refund(self, refund_id: uuid.UUID) -> CreditLedgerEntry | None:
        return await self.session.scalar(
            select(CreditLedgerEntry).where(CreditLedgerEntry.refund_id == refund_id)
        )

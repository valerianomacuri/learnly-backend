import uuid
from collections.abc import Collection

from sqlalchemy import select

from app.models.refund import RefundPayout
from app.repositories.base import BaseRepository


class RefundPayoutRepository(BaseRepository[RefundPayout]):
    model = RefundPayout

    async def get_by_refund(
        self, refund_id: uuid.UUID, *, for_update: bool = False
    ) -> RefundPayout | None:
        stmt = select(RefundPayout).where(RefundPayout.refund_id == refund_id)
        if for_update:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def map_for_refunds(
        self, refund_ids: Collection[uuid.UUID]
    ) -> dict[uuid.UUID, RefundPayout]:
        if not refund_ids:
            return {}
        result = await self.session.scalars(
            select(RefundPayout).where(RefundPayout.refund_id.in_(refund_ids))
        )
        return {p.refund_id: p for p in result}

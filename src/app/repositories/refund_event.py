import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.models.refund import RefundEvent
from app.repositories.base import BaseRepository


class RefundEventRepository(BaseRepository[RefundEvent]):
    """Insert and read only: the table rejects UPDATE and DELETE (trigger)."""

    model = RefundEvent

    async def list_for_refund(self, refund_id: uuid.UUID) -> Sequence[RefundEvent]:
        result = await self.session.scalars(
            select(RefundEvent)
            .where(RefundEvent.refund_id == refund_id)
            .order_by(RefundEvent.created_at, RefundEvent.id)
        )
        return result.all()

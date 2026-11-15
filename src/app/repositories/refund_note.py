import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.models.refund import RefundInternalNote
from app.repositories.base import BaseRepository


class RefundNoteRepository(BaseRepository[RefundInternalNote]):
    """Supervisor-only notes: never reachable from a customer-facing service."""

    model = RefundInternalNote

    async def list_for_refund(
        self, refund_id: uuid.UUID
    ) -> Sequence[RefundInternalNote]:
        result = await self.session.scalars(
            select(RefundInternalNote)
            .where(RefundInternalNote.refund_id == refund_id)
            .order_by(RefundInternalNote.created_at, RefundInternalNote.id)
        )
        return result.all()

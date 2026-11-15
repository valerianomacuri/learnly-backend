import uuid
from datetime import datetime

from sqlalchemy import func, select

from app.domain.enums import RefundStatus
from app.models.refund import RefundRequest
from app.repositories.base import BaseRepository


class RefundRequestRepository(BaseRepository[RefundRequest]):
    """Queries that feed the eligibility facts (owner-scoped by the caller)."""

    model = RefundRequest

    async def statuses_for_purchase(
        self, purchase_id: uuid.UUID, customer_id: uuid.UUID
    ) -> tuple[RefundStatus, ...]:
        result = await self.session.scalars(
            select(RefundRequest.status).where(
                RefundRequest.purchase_id == purchase_id,
                RefundRequest.customer_id == customer_id,
            )
        )
        return tuple(result.all())

    async def count_completed_since(
        self, customer_id: uuid.UUID, since: datetime
    ) -> int:
        """COMPLETED refunds of the customer decided (or created) on/after `since`."""
        moment = func.coalesce(RefundRequest.decided_at, RefundRequest.created_at)
        count = await self.session.scalar(
            select(func.count())
            .select_from(RefundRequest)
            .where(
                RefundRequest.customer_id == customer_id,
                RefundRequest.status == RefundStatus.COMPLETED,
                moment >= since,
            )
        )
        return int(count or 0)

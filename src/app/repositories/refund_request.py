import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, tuple_

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

    async def get_owned(
        self, refund_id: uuid.UUID, customer_id: uuid.UUID, *, for_update: bool = False
    ) -> RefundRequest | None:
        """The customer's own refund; someone else's is None (looks missing)."""
        stmt = select(RefundRequest).where(
            RefundRequest.id == refund_id, RefundRequest.customer_id == customer_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def get_by_idempotency_key(
        self, customer_id: uuid.UUID, key: str
    ) -> RefundRequest | None:
        return await self.session.scalar(
            select(RefundRequest).where(
                RefundRequest.customer_id == customer_id,
                RefundRequest.idempotency_key == key,
            )
        )

    async def list_owned(
        self,
        customer_id: uuid.UUID,
        *,
        status: RefundStatus | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int,
    ) -> Sequence[RefundRequest]:
        """Newest first, keyset-paginated on `(created_at, id)`."""
        stmt = select(RefundRequest).where(RefundRequest.customer_id == customer_id)
        if status is not None:
            stmt = stmt.where(RefundRequest.status == status)
        if after is not None:
            stmt = stmt.where(
                tuple_(RefundRequest.created_at, RefundRequest.id) < tuple_(*after)
            )
        result = await self.session.scalars(
            stmt.order_by(
                RefundRequest.created_at.desc(), RefundRequest.id.desc()
            ).limit(limit)
        )
        return result.all()

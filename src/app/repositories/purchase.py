import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select, tuple_
from sqlalchemy.orm import selectinload

from app.domain.enums import PurchaseStatus
from app.models.purchase import Purchase
from app.repositories.base import BaseRepository


class PurchaseRepository(BaseRepository[Purchase]):
    """Purchases are only readable by their owner in this spec.

    A supervisor sees a purchase through the refund request detail (spec 04),
    so there is no `*_any` scope here: every query filters by `customer_id`.
    """

    model = Purchase

    async def get_owned(
        self, purchase_id: uuid.UUID, customer_id: uuid.UUID
    ) -> Purchase | None:
        """The purchase with its course and progress; None if missing or not theirs."""
        return await self.session.scalar(
            select(Purchase)
            .where(Purchase.id == purchase_id, Purchase.customer_id == customer_id)
            .options(selectinload(Purchase.course), selectinload(Purchase.progress))
        )

    async def list_owned(
        self,
        customer_id: uuid.UUID,
        *,
        status: PurchaseStatus | None = None,
        purchased_from: datetime | None = None,
        purchased_before: datetime | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int,
    ) -> Sequence[Purchase]:
        """Newest first, keyset-paginated on `(purchased_at, id)`.

        `purchased_from` is inclusive and `purchased_before` exclusive, so the two
        partition the purchases around the refund-window cutoff.
        """
        stmt = select(Purchase).where(Purchase.customer_id == customer_id)
        if status is not None:
            stmt = stmt.where(Purchase.status == status)
        if purchased_from is not None:
            stmt = stmt.where(Purchase.purchased_at >= purchased_from)
        if purchased_before is not None:
            stmt = stmt.where(Purchase.purchased_at < purchased_before)
        if after is not None:
            stmt = stmt.where(
                tuple_(Purchase.purchased_at, Purchase.id) < tuple_(*after)
            )
        result = await self.session.scalars(
            stmt.options(selectinload(Purchase.course))
            .order_by(Purchase.purchased_at.desc(), Purchase.id.desc())
            .limit(limit)
        )
        return result.all()

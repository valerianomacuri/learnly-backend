import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import decode_cursor, encode_cursor
from app.core.permissions import Perm
from app.core.security import Principal
from app.domain.enums import PurchaseStatus
from app.models.purchase import Purchase
from app.repositories.purchase import PurchaseRepository
from app.schemas.purchase import (
    CourseSummary,
    ProgressRead,
    PurchaseDetail,
    PurchasePage,
    PurchaseRead,
)
from app.services.authz import require_permission
from app.services.errors import InvalidInputError, NotFoundError

# TODO(spec-02): replaced by `PolicyConfig.refund_window_days` (policy front-matter).
REFUND_WINDOW_DAYS = 30


def utc_now() -> datetime:
    return datetime.now(UTC)


class PurchaseService:
    """Reads purchases for their owner. Someone else's purchase is a 404."""

    def __init__(
        self, session: AsyncSession, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self.repo = PurchaseRepository(session)
        self.clock = clock

    async def list(
        self,
        principal: Principal,
        *,
        status: PurchaseStatus | None = None,
        within_window: bool | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> PurchasePage:
        require_permission(principal, Perm.PURCHASE_READ_OWN)
        now = self.clock()
        after = None
        if cursor is not None:
            try:
                after = decode_cursor(cursor)
            except ValueError as exc:
                raise InvalidInputError("Invalid cursor") from exc

        window_start = now - timedelta(days=REFUND_WINDOW_DAYS)
        rows = await self.repo.list_owned(
            principal.user_id,
            status=status,
            purchased_from=window_start if within_window is True else None,
            purchased_before=window_start if within_window is False else None,
            after=after,
            limit=limit + 1,  # one extra row tells whether another page exists
        )
        page = rows[:limit]
        next_cursor = (
            encode_cursor(page[-1].purchased_at, page[-1].id)
            if len(rows) > limit
            else None
        )
        return PurchasePage(
            items=[self._read(p, now) for p in page], next_cursor=next_cursor
        )

    async def get(self, principal: Principal, purchase_id: uuid.UUID) -> PurchaseDetail:
        require_permission(principal, Perm.PURCHASE_READ_OWN)
        purchase = await self.repo.get_owned(purchase_id, principal.user_id)
        if purchase is None:
            raise NotFoundError("Purchase")
        progress = purchase.progress
        return PurchaseDetail(
            **self._read(purchase, self.clock()).model_dump(),
            progress=ProgressRead.model_validate(progress) if progress else None,
        )

    @staticmethod
    def refund_deadline(purchase: Purchase) -> datetime:
        return purchase.purchased_at + timedelta(days=REFUND_WINDOW_DAYS)

    def _read(self, purchase: Purchase, now: datetime) -> PurchaseRead:
        deadline = self.refund_deadline(purchase)
        return PurchaseRead(
            id=purchase.id,
            course=CourseSummary.model_validate(purchase.course),
            amount=purchase.amount,
            currency=purchase.currency,
            payment_method=purchase.payment_method,
            purchase_type=purchase.purchase_type,
            purchased_at=purchase.purchased_at,
            status=purchase.status,
            refund_deadline=deadline,
            within_refund_window=now <= deadline,  # inclusive limit (BR-02)
        )

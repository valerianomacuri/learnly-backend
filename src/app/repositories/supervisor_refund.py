"""Supervisor-side queries over refund requests (any customer's, no owner scoping).

Authorization is the service's job (`refund:read_any` / `refund:decide`); this
module only builds queries.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, and_, func, not_, or_, select, tuple_

from app.domain.enums import (
    Channel,
    EligibilityWarning,
    QueueSort,
    ReasonCategory,
    RefundStatus,
)
from app.models.course import Course
from app.models.purchase import CourseProgress, Purchase
from app.models.refund import RefundRequest
from app.models.user import User
from app.repositories.base import BaseRepository


@dataclass(frozen=True, slots=True)
class QueueQuery:
    """A fully typed queue request. `after` is `(sort value, id)` of the last row."""

    sla_cutoff: datetime  # PENDING_APPROVAL older than this is over its SLA
    sort: QueueSort = QueueSort.CREATED_AT
    descending: bool = True
    limit: int = 20
    after: tuple[datetime | Decimal, uuid.UUID] | None = None
    status: RefundStatus | None = None
    sla_breached: bool | None = None
    high_risk: bool | None = None
    channel: Channel | None = None
    reason_category: ReasonCategory | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    customer: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionStats:
    decided: int
    approved: int
    avg_decision_seconds: float | None


class SupervisorRefundRepository(BaseRepository[RefundRequest]):
    model = RefundRequest

    @staticmethod
    def sla_breached_clause(cutoff: datetime) -> ColumnElement[bool]:
        """BR-21: computed, never stored. Only a pending request can breach."""
        return and_(
            RefundRequest.status == RefundStatus.PENDING_APPROVAL,
            RefundRequest.created_at < cutoff,
        )

    @staticmethod
    def high_risk_clause() -> ColumnElement[bool]:
        return RefundRequest.eligibility_snapshot.contains(
            {"warnings": [EligibilityWarning.HIGH_REFUND_FREQUENCY.value]}
        )

    async def list_queue(
        self, query: QueueQuery
    ) -> Sequence[tuple[RefundRequest, User]]:
        stmt = select(RefundRequest, User).join(
            User, User.id == RefundRequest.customer_id
        )
        if query.status is not None:
            stmt = stmt.where(RefundRequest.status == query.status)
        if query.sla_breached is not None:
            clause = self.sla_breached_clause(query.sla_cutoff)
            stmt = stmt.where(clause if query.sla_breached else not_(clause))
        if query.high_risk is not None:
            clause = self.high_risk_clause()
            stmt = stmt.where(clause if query.high_risk else not_(clause))
        if query.channel is not None:
            stmt = stmt.where(RefundRequest.channel == query.channel)
        if query.reason_category is not None:
            stmt = stmt.where(RefundRequest.reason_category == query.reason_category)
        if query.created_from is not None:
            stmt = stmt.where(RefundRequest.created_at >= query.created_from)
        if query.created_to is not None:
            stmt = stmt.where(RefundRequest.created_at <= query.created_to)
        if query.customer:
            stmt = stmt.where(
                or_(
                    User.name.icontains(query.customer, autoescape=True),
                    User.email.icontains(query.customer, autoescape=True),
                )
            )

        column = (
            RefundRequest.amount
            if query.sort is QueueSort.AMOUNT
            else RefundRequest.created_at
        )
        if query.after is not None:
            keys = tuple_(column, RefundRequest.id)
            stmt = stmt.where(
                keys < tuple_(*query.after)
                if query.descending
                else keys > tuple_(*query.after)
            )
        order = (
            (column.desc(), RefundRequest.id.desc())
            if query.descending
            else (column.asc(), RefundRequest.id.asc())
        )
        result = await self.session.execute(stmt.order_by(*order).limit(query.limit))
        return [(row[0], row[1]) for row in result.all()]

    async def get_with_customer(
        self, refund_id: uuid.UUID
    ) -> tuple[RefundRequest, User] | None:
        result = await self.session.execute(
            select(RefundRequest, User)
            .join(User, User.id == RefundRequest.customer_id)
            .where(RefundRequest.id == refund_id)
        )
        row = result.first()
        return None if row is None else (row[0], row[1])

    async def get_for_update(self, refund_id: uuid.UUID) -> RefundRequest | None:
        """Lock the row so concurrent decisions serialize (the loser re-reads it)."""
        return await self.session.scalar(
            select(RefundRequest)
            .where(RefundRequest.id == refund_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def purchase_review(
        self, purchase_id: uuid.UUID
    ) -> tuple[Purchase, Course, CourseProgress | None] | None:
        result = await self.session.execute(
            select(Purchase, Course, CourseProgress)
            .join(Course, Course.id == Purchase.course_id)
            .outerjoin(CourseProgress, CourseProgress.purchase_id == Purchase.id)
            .where(Purchase.id == purchase_id)
        )
        row = result.first()
        return None if row is None else (row[0], row[1], row[2])

    async def counts_by_status(self, customer_id: uuid.UUID) -> dict[str, int]:
        result = await self.session.execute(
            select(RefundRequest.status, func.count())
            .where(RefundRequest.customer_id == customer_id)
            .group_by(RefundRequest.status)
        )
        return {str(status): int(n) for status, n in result.all()}

    async def recent_for_customer(
        self, customer_id: uuid.UUID, *, limit: int = 5
    ) -> Sequence[RefundRequest]:
        result = await self.session.scalars(
            select(RefundRequest)
            .where(RefundRequest.customer_id == customer_id)
            .order_by(RefundRequest.created_at.desc(), RefundRequest.id.desc())
            .limit(limit)
        )
        return result.all()

    async def count_pending(self) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(RefundRequest)
                .where(RefundRequest.status == RefundStatus.PENDING_APPROVAL)
            )
            or 0
        )

    async def count_sla_breached(self, cutoff: datetime) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(RefundRequest)
                .where(self.sla_breached_clause(cutoff))
            )
            or 0
        )

    async def decision_stats(self) -> DecisionStats:
        """Requests a supervisor decided (`decided_by` set), approved or rejected."""
        decided = RefundRequest.decided_by.is_not(None)
        row = (
            await self.session.execute(
                select(
                    func.count(),
                    func.count().filter(RefundRequest.status != RefundStatus.REJECTED),
                    func.avg(
                        func.extract(
                            "epoch", RefundRequest.decided_at - RefundRequest.created_at
                        )
                    ),
                ).where(decided)
            )
        ).one()
        avg = row[2]
        return DecisionStats(
            decided=int(row[0]),
            approved=int(row[1]),
            avg_decision_seconds=None if avg is None else float(avg),
        )

    async def top_reasons(self, *, limit: int = 5) -> list[tuple[ReasonCategory, int]]:
        count = func.count()
        result = await self.session.execute(
            select(RefundRequest.reason_category, count)
            .group_by(RefundRequest.reason_category)
            .order_by(count.desc(), RefundRequest.reason_category)
            .limit(limit)
        )
        return [(category, int(n)) for category, n in result.all()]

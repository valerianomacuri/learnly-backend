import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.pagination import decode_keyset, encode_keyset
from app.core.permissions import Perm
from app.core.privacy import mask_email
from app.core.security import Principal
from app.domain.enums import (
    ActorType,
    Channel,
    EligibilityWarning,
    QueueSort,
    ReasonCategory,
    RefundStatus,
    SortOrder,
)
from app.domain.policy_config import ELIGIBILITY_DOC_KEY
from app.domain.state_machine import transition
from app.models.refund import RefundInternalNote, RefundRequest
from app.models.user import User
from app.repositories.policy import PolicyRepository
from app.repositories.refund_event import RefundEventRepository
from app.repositories.refund_note import RefundNoteRepository
from app.repositories.supervisor_refund import QueueQuery, SupervisorRefundRepository
from app.schemas.purchase import CourseSummary, ProgressRead
from app.schemas.refund import Citation
from app.schemas.supervisor import (
    CustomerHistory,
    CustomerSummary,
    HistoryItem,
    NoteRead,
    PurchaseReview,
    ReasonCount,
    RefundFlags,
    SupervisorEventRead,
    SupervisorRefundDetail,
    SupervisorRefundPage,
    SupervisorRefundRead,
    SupervisorSnapshot,
    SupervisorStats,
)
from app.services.authz import require_permission
from app.services.errors import (
    DecisionNoteRequired,
    InvalidInputError,
    NotFoundError,
    RefundNotPending,
    SelfApprovalForbidden,
    VersionConflict,
)
from app.services.refund_event_service import EventActor, RefundEventService

EVENT_APPROVED = "REFUND_APPROVED"
EVENT_REJECTED = "REFUND_REJECTED"
# Internal on purpose: not in `refund_service.PUBLIC_EVENT_TYPES`, so the customer
# timeline filters it out. The payload carries the note id, never its text.
EVENT_NOTE_ADDED = "NOTE_ADDED"

MIN_REJECTION_NOTE = 10
_DEFAULT_DESCENDING = {
    QueueSort.CREATED_AT: True,
    QueueSort.AMOUNT: True,
    QueueSort.SLA: False,  # most overdue (oldest) first
}


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class QueueFilters:
    status: RefundStatus | None = None
    sla_breached: bool | None = None
    high_risk: bool | None = None
    channel: Channel | None = None
    reason_category: ReasonCategory | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    customer: str | None = None
    sort: QueueSort = QueueSort.CREATED_AT
    order: SortOrder | None = None  # default depends on `sort`
    cursor: str | None = None
    limit: int = 20


class SupervisorRefundService:
    """Review and decide refund requests of any customer (spec 04).

    Takes an injectable `clock` and `sla_hours` so the SLA is testable. Approving
    only moves the state and writes the event; paying is the worker's job (spec 05).
    """

    def __init__(
        self,
        session: AsyncSession,
        clock: Callable[[], datetime] = utc_now,
        sla_hours: int | None = None,
    ) -> None:
        self.session = session
        self.clock = clock
        self.sla = timedelta(hours=sla_hours or get_settings().sla_hours)
        self.refunds = SupervisorRefundRepository(session)
        self.notes = RefundNoteRepository(session)
        self.event_reads = RefundEventRepository(session)
        self.events = RefundEventService(session)
        self.policies = PolicyRepository(session)

    # ----------------------------------------------------------------- queue

    async def list_queue(
        self, principal: Principal, filters: QueueFilters
    ) -> SupervisorRefundPage:
        require_permission(principal, Perm.REFUND_READ_ANY)
        sort = filters.sort
        descending = (
            _DEFAULT_DESCENDING[sort]
            if filters.order is None
            else filters.order is SortOrder.DESC
        )
        customer = (filters.customer or "").strip() or None
        rows = await self.refunds.list_queue(
            QueueQuery(
                sla_cutoff=self._cutoff(),
                sort=sort,
                descending=descending,
                limit=filters.limit + 1,
                after=self._decode_after(filters.cursor, sort),
                status=filters.status,
                sla_breached=filters.sla_breached,
                high_risk=filters.high_risk,
                channel=filters.channel,
                reason_category=filters.reason_category,
                created_from=filters.created_from,
                created_to=filters.created_to,
                customer=customer,
            )
        )
        page = rows[: filters.limit]
        next_cursor = None
        if len(rows) > filters.limit:
            last = page[-1][0]
            value = (
                str(last.amount)
                if sort is QueueSort.AMOUNT
                else last.created_at.isoformat()
            )
            next_cursor = encode_keyset(value, last.id)
        return SupervisorRefundPage(
            items=[self._to_read(refund, user) for refund, user in page],
            next_cursor=next_cursor,
        )

    @staticmethod
    def _decode_after(
        cursor: str | None, sort: QueueSort
    ) -> tuple[datetime | Decimal, uuid.UUID] | None:
        if cursor is None:
            return None
        try:
            raw, row_id = decode_keyset(cursor)
            if sort is QueueSort.AMOUNT:
                amount = Decimal(raw)
                if not amount.is_finite():
                    raise ValueError("cursor amount must be finite")
                return amount, row_id
            moment = datetime.fromisoformat(raw)
            if moment.tzinfo is None:
                raise ValueError("cursor timestamp must be timezone-aware")
            return moment, row_id
        except (ValueError, InvalidOperation) as exc:
            raise InvalidInputError("Invalid cursor") from exc

    # ---------------------------------------------------------------- detail

    async def get_detail(
        self, principal: Principal, refund_id: uuid.UUID
    ) -> SupervisorRefundDetail:
        require_permission(principal, Perm.REFUND_READ_ANY)
        found = await self.refunds.get_with_customer(refund_id)
        if found is None:
            raise NotFoundError("Refund")
        refund, customer = found
        base = self._to_read(refund, customer)
        snapshot = SupervisorSnapshot.model_validate(refund.eligibility_snapshot)

        review = await self.refunds.purchase_review(refund.purchase_id)
        if review is None:  # the FK makes this unreachable; keep the type honest
            raise NotFoundError("Purchase")
        purchase, course, progress = review

        notes = (
            await self.notes.list_for_refund(refund.id)
            if principal.has(Perm.NOTE_READ)
            else []
        )
        return SupervisorRefundDetail(
            **base.model_dump(),
            eligibility=snapshot,
            purchase=PurchaseReview(
                id=purchase.id,
                course=CourseSummary.model_validate(course),
                amount=purchase.amount,
                currency=purchase.currency,
                payment_method=purchase.payment_method,
                purchase_type=purchase.purchase_type,
                purchased_at=purchase.purchased_at,
                status=purchase.status,
                progress=(
                    ProgressRead.model_validate(progress)
                    if progress is not None
                    else None
                ),
            ),
            customer_history=await self._history(refund.customer_id),
            citations=await self._citations(snapshot),
            notes=[NoteRead.model_validate(n) for n in notes],
        )

    async def _history(self, customer_id: uuid.UUID) -> CustomerHistory:
        by_status = await self.refunds.counts_by_status(customer_id)
        recent = await self.refunds.recent_for_customer(customer_id)
        return CustomerHistory(
            total=sum(by_status.values()),
            by_status=by_status,
            recent=[
                HistoryItem(
                    id=r.id,
                    status=r.status,
                    reason_category=r.reason_category,
                    amount=r.amount,
                    created_at=r.created_at,
                )
                for r in recent
            ],
        )

    async def _citations(self, snapshot: SupervisorSnapshot) -> list[Citation]:
        """Policy sections behind the snapshot's reason codes and warnings."""
        config = await self.policies.get_active_config()
        keys = [str(c) for c in snapshot.reason_codes] + [
            str(w) for w in snapshot.warnings
        ]
        return [
            Citation(doc_key=ELIGIBILITY_DOC_KEY, section_path=path)
            for key in keys
            if (path := config.reason_code_map.get(key))
        ]

    # -------------------------------------------------------------- decision

    async def approve(
        self,
        principal: Principal,
        refund_id: uuid.UUID,
        *,
        if_match: int,
        note: str | None = None,
    ) -> SupervisorRefundRead:
        return await self._decide(
            principal, refund_id, if_match, note, RefundStatus.APPROVED
        )

    async def reject(
        self,
        principal: Principal,
        refund_id: uuid.UUID,
        *,
        if_match: int,
        note: str | None,
    ) -> SupervisorRefundRead:
        return await self._decide(
            principal, refund_id, if_match, note, RefundStatus.REJECTED
        )

    async def _decide(
        self,
        principal: Principal,
        refund_id: uuid.UUID,
        if_match: int,
        note: str | None,
        target: RefundStatus,
    ) -> SupervisorRefundRead:
        """Order matters: permission, lock, self-approval, state, version, note.

        The row lock serializes concurrent decisions: the loser waits, re-reads the
        decided row and answers 409. Decisions are immutable (BR-17): once the
        status leaves PENDING_APPROVAL nothing here can run again.
        """
        require_permission(principal, Perm.REFUND_DECIDE)
        refund = await self.refunds.get_for_update(refund_id)
        if refund is None:
            raise NotFoundError("Refund")
        if refund.customer_id == principal.user_id:  # BR-20
            raise SelfApprovalForbidden
        if refund.status is not RefundStatus.PENDING_APPROVAL:
            raise RefundNotPending
        if refund.version != if_match:  # BR-18
            raise VersionConflict
        clean = (note or "").strip() or None
        if target is RefundStatus.REJECTED and (
            clean is None or len(clean) < MIN_REJECTION_NOTE
        ):  # BR-19
            raise DecisionNoteRequired

        now = self.clock()
        previous = refund.status
        refund.status = transition(previous, target, ActorType.SUPERVISOR)
        refund.decided_by = principal.user_id
        refund.decided_at = now
        refund.decision_note = clean
        refund.version += 1
        refund.updated_at = now
        await self.session.flush()
        await self.events.append(
            refund.id,
            EVENT_APPROVED if target is RefundStatus.APPROVED else EVENT_REJECTED,
            EventActor(ActorType.SUPERVISOR, principal.user_id),
            {"from": previous.value, "to": target.value, "version": refund.version},
        )
        customer = await self.session.get(User, refund.customer_id)
        assert customer is not None  # FK
        return self._to_read(refund, customer)

    # ----------------------------------------------------------------- notes

    async def add_note(
        self, principal: Principal, refund_id: uuid.UUID, body: str
    ) -> NoteRead:
        require_permission(principal, Perm.NOTE_WRITE)
        if await self.refunds.get(refund_id) is None:
            raise NotFoundError("Refund")
        note = await self.notes.add(
            RefundInternalNote(
                refund_id=refund_id, supervisor_id=principal.user_id, body=body
            )
        )
        await self.events.append(
            refund_id,
            EVENT_NOTE_ADDED,
            EventActor(ActorType.SUPERVISOR, principal.user_id),
            {"note_id": str(note.id)},
        )
        return NoteRead.model_validate(note)

    async def list_notes(
        self, principal: Principal, refund_id: uuid.UUID
    ) -> Sequence[NoteRead]:
        require_permission(principal, Perm.NOTE_READ)
        if await self.refunds.get(refund_id) is None:
            raise NotFoundError("Refund")
        notes = await self.notes.list_for_refund(refund_id)
        return [NoteRead.model_validate(n) for n in notes]

    # ---------------------------------------------------------------- events

    async def timeline(
        self, principal: Principal, refund_id: uuid.UUID
    ) -> Sequence[SupervisorEventRead]:
        """Every event, internal ones and payloads included."""
        require_permission(principal, Perm.REFUND_READ_ANY)
        if await self.refunds.get(refund_id) is None:
            raise NotFoundError("Refund")
        events = await self.event_reads.list_for_refund(refund_id)
        return [
            SupervisorEventRead(
                type=e.type,
                actor_type=e.actor_type,
                actor_id=e.actor_id,
                payload=e.payload,
                created_at=e.created_at,
            )
            for e in events
        ]

    # ----------------------------------------------------------------- stats

    async def stats(self, principal: Principal) -> SupervisorStats:
        """Plain aggregate queries; no cache yet."""
        require_permission(principal, Perm.REFUND_READ_ANY)
        decisions = await self.refunds.decision_stats()
        return SupervisorStats(
            pending=await self.refunds.count_pending(),
            sla_breached=await self.refunds.count_sla_breached(self._cutoff()),
            avg_decision_seconds=decisions.avg_decision_seconds,
            approval_rate=(
                decisions.approved / decisions.decided if decisions.decided else None
            ),
            top_reasons=[
                ReasonCount(reason_category=category, count=count)
                for category, count in await self.refunds.top_reasons()
            ],
        )

    # --------------------------------------------------------------- helpers

    def _cutoff(self) -> datetime:
        return self.clock() - self.sla

    def _to_read(self, refund: RefundRequest, customer: User) -> SupervisorRefundRead:
        snapshot = SupervisorSnapshot.model_validate(refund.eligibility_snapshot)
        pending = refund.status is RefundStatus.PENDING_APPROVAL
        return SupervisorRefundRead(
            id=refund.id,
            purchase_id=refund.purchase_id,
            customer=CustomerSummary(
                id=customer.id, name=customer.name, email=mask_email(customer.email)
            ),
            status=refund.status,
            reason_category=refund.reason_category,
            reason_text=refund.reason_text,
            refund_method=refund.refund_method,
            amount=refund.amount,
            currency=refund.currency,
            channel=refund.channel,
            policy_version=refund.policy_version,
            version=refund.version,
            created_at=refund.created_at,
            updated_at=refund.updated_at,
            sla_due_at=refund.created_at + self.sla if pending else None,
            flags=RefundFlags(
                sla_breached=pending and refund.created_at < self._cutoff(),
                high_risk=EligibilityWarning.HIGH_REFUND_FREQUENCY in snapshot.warnings,
                warnings=snapshot.warnings,
            ),
            decided_by=refund.decided_by,
            decided_at=refund.decided_at,
            decision_note=refund.decision_note,
        )

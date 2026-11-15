import hashlib
import json
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.pagination import decode_cursor, encode_cursor
from app.core.permissions import Perm
from app.core.security import Principal
from app.domain import confirmation
from app.domain.enums import ActorType, Channel, CheckSource, RefundStatus
from app.domain.policy_config import ELIGIBILITY_DOC_KEY
from app.domain.reason import validate_reason
from app.domain.state_machine import transition
from app.models.refund import RefundPayout, RefundRequest
from app.repositories.payout import RefundPayoutRepository
from app.repositories.policy import PolicyRepository
from app.repositories.purchase import PurchaseRepository
from app.repositories.refund_event import RefundEventRepository
from app.repositories.refund_request import RefundRequestRepository
from app.schemas.refund import (
    Citation,
    PayoutRead,
    PreviewRequest,
    PreviewResult,
    RefundCreate,
    RefundEventRead,
    RefundPage,
    RefundRead,
)
from app.services.authz import require_permission
from app.services.eligibility_service import EligibilityService
from app.services.errors import (
    IdempotencyConflict,
    InvalidInputError,
    NotFoundError,
    RefundNotEligible,
    VersionConflict,
)
from app.services.refund_event_service import EventActor, RefundEventService

# Event types a customer may see in the timeline; anything else is internal.
EVENT_CREATED = "REFUND_CREATED"
EVENT_CANCELLED = "REFUND_CANCELLED"
PUBLIC_EVENT_TYPES = frozenset(
    {
        EVENT_CREATED,
        "REFUND_APPROVED",
        "REFUND_REJECTED",
        EVENT_CANCELLED,
        "REFUND_PROCESSING",
        "REFUND_COMPLETED",
        "REFUND_FAILED",
    }
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def request_hash(req: PreviewRequest) -> str:
    """BR-15: identifies the body of a creation (the token is not part of it)."""
    body = {
        "purchase_id": str(req.purchase_id),
        "reason_category": str(req.reason_category),
        "reason_text": req.reason_text,
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class RefundService:
    """Single domain entry point to create refunds (form endpoint and, later, agent).

    Persistence of failed attempts (D6): `create_request` flushes the
    `eligibility_checks` row and then raises `RefundNotEligible`. `get_session`
    rolls back on exceptions, so the route catches it and answers the 422 itself
    (it returns instead of raising), which lets the request commit.
    """

    def __init__(
        self, session: AsyncSession, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self.session = session
        self.clock = clock
        self.eligibility = EligibilityService(session, clock)
        self.purchases = PurchaseRepository(session)
        self.refunds = RefundRequestRepository(session)
        self.events = RefundEventService(session)
        self.event_reads = RefundEventRepository(session)
        self.policies = PolicyRepository(session)
        self.payouts = RefundPayoutRepository(session)

    @staticmethod
    def _secret() -> str:
        secret = get_settings().confirmation_secret
        assert secret is not None  # derived from JWT_SECRET by the settings
        return secret.get_secret_value()

    async def preview(self, principal: Principal, req: PreviewRequest) -> PreviewResult:
        """Evaluate and, if eligible, issue the confirmation token. Writes no refund."""
        require_permission(principal, Perm.REFUND_SIMULATE)
        category, text = validate_reason(req.reason_category, req.reason_text)
        config = await self.policies.get_active_config()
        result, purchase = await self.eligibility.evaluate(
            principal, req.purchase_id, CheckSource.PREVIEW
        )

        token: str | None = None
        expires_at: datetime | None = None
        citations: list[Citation] = []
        if result.eligible:
            assert result.amount is not None and result.refund_method is not None
            token, expires_at = confirmation.issue_token(
                secret=self._secret(),
                customer_id=principal.user_id,
                hash=confirmation.payload_hash(
                    purchase_id=req.purchase_id,
                    reason_category=category,
                    reason_text=text,
                    amount=result.amount,
                    refund_method=result.refund_method,
                ),
                now=self.clock(),
                ttl_min=config.confirmation_ttl_min,
            )
        else:
            citations = [
                Citation(doc_key=ELIGIBILITY_DOC_KEY, section_path=path)
                for code in result.reason_codes
                if (path := config.reason_code_map.get(code.value))
            ]
        return PreviewResult(
            eligible=result.eligible,
            reason_codes=result.reason_codes,
            purchase_id=req.purchase_id,
            amount=result.amount,
            currency=purchase.currency if purchase else None,
            refund_method=result.refund_method,
            days_since_purchase=result.days_since_purchase,
            refund_window_days=result.refund_window_days,
            progress_pct=result.progress_pct,
            policy_version=result.policy_version,
            citations=citations,
            confirmation_token=token,
            expires_at=expires_at,
        )

    async def create_request(
        self,
        principal: Principal,
        req: RefundCreate,
        *,
        idempotency_key: str,
        confirmation_proof: str | None,
        channel: Channel,
        chat_thread_id: uuid.UUID | None = None,
    ) -> RefundRead:
        """Create the refund in one transaction (BR-11...BR-16).

        Order: permission, lock the purchase, idempotency, reason, re-evaluation,
        token, insert, event. Raises `RefundNotEligible` after recording the check.
        """
        require_permission(principal, Perm.REFUND_CREATE)
        body_hash = request_hash(req)

        # The row lock serializes concurrent creations on the same purchase: the
        # losers wait, then find the winner's row through the idempotency key.
        await self.purchases.get_owned(
            req.purchase_id, principal.user_id, for_update=True
        )
        existing = await self.refunds.get_by_idempotency_key(
            principal.user_id, idempotency_key
        )
        if existing is not None:
            return self._replay(existing, body_hash)

        category, text = validate_reason(req.reason_category, req.reason_text)
        result, purchase = await self.eligibility.evaluate(
            principal, req.purchase_id, CheckSource.CREATE_ATTEMPT
        )
        if (
            not result.eligible
            or purchase is None
            or result.amount is None
            or result.refund_method is None
        ):
            raise RefundNotEligible(
                reason_codes=[code.value for code in result.reason_codes]
            )

        now = self.clock()
        proof_hash = confirmation.payload_hash(
            purchase_id=req.purchase_id,
            reason_category=category,
            reason_text=text,
            amount=result.amount,
            refund_method=result.refund_method,
        )
        confirmation.verify(
            confirmation_proof,
            secret=self._secret(),
            payload_hash=proof_hash,
            customer_id=principal.user_id,
            now=now,
        )

        refund = RefundRequest(
            customer_id=principal.user_id,
            purchase_id=req.purchase_id,
            status=RefundStatus.PENDING_APPROVAL,
            reason_category=category,
            reason_text=text,
            refund_method=result.refund_method,
            amount=result.amount,
            currency=purchase.currency,
            channel=channel,
            chat_thread_id=chat_thread_id,
            eligibility_snapshot=result.model_dump(mode="json"),
            policy_version=result.policy_version,
            confirmed_at=now,
            confirmation_hash=proof_hash,
            idempotency_key=idempotency_key,
            request_hash=body_hash,
            created_at=now,  # BR-16: the window is measured up to this instant
            updated_at=now,
        )
        try:
            async with self.session.begin_nested():
                await self.refunds.add(refund)
        except IntegrityError:
            # Lost a race the lock could not order (same key on another purchase).
            winner = await self.refunds.get_by_idempotency_key(
                principal.user_id, idempotency_key
            )
            if winner is None:
                raise
            return self._replay(winner, body_hash)

        await self.events.append(
            refund.id,
            EVENT_CREATED,
            EventActor(
                ActorType.AGENT if channel is Channel.CHAT else ActorType.CUSTOMER,
                principal.user_id,
            ),
            {"channel": channel.value, "policy_version": result.policy_version},
        )
        return RefundRead.model_validate(refund)

    @staticmethod
    def _read(refund: RefundRequest, payout: RefundPayout | None) -> RefundRead:
        read = RefundRead.model_validate(refund)
        if payout is not None:
            read.payout = PayoutRead.model_validate(payout)
        return read

    @staticmethod
    def _replay(existing: RefundRequest, body_hash: str) -> RefundRead:
        """Same key and body: the original response (201 again); other body: 409."""
        if existing.request_hash != body_hash:
            raise IdempotencyConflict
        return RefundRead.model_validate(existing)

    async def cancel(
        self, principal: Principal, refund_id: uuid.UUID, *, if_match: int
    ) -> RefundRead:
        """BR-24: only the owner, only while PENDING_APPROVAL, optimistic lock."""
        require_permission(principal, Perm.REFUND_CANCEL_OWN)
        refund = await self.refunds.get_owned(
            refund_id, principal.user_id, for_update=True
        )
        if refund is None:
            raise NotFoundError("Refund")
        if refund.version != if_match:
            raise VersionConflict
        refund.status = transition(
            refund.status, RefundStatus.CANCELLED, ActorType.CUSTOMER
        )
        refund.version += 1
        refund.updated_at = self.clock()
        await self.session.flush()
        await self.events.append(
            refund.id,
            EVENT_CANCELLED,
            EventActor(ActorType.CUSTOMER, principal.user_id),
        )
        return RefundRead.model_validate(refund)

    async def get(self, principal: Principal, refund_id: uuid.UUID) -> RefundRead:
        require_permission(principal, Perm.REFUND_READ_OWN)
        refund = await self.refunds.get_owned(refund_id, principal.user_id)
        if refund is None:
            raise NotFoundError("Refund")
        payout = await self.payouts.get_by_refund(refund.id)
        return self._read(refund, payout)

    async def list(
        self,
        principal: Principal,
        *,
        status: RefundStatus | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> RefundPage:
        require_permission(principal, Perm.REFUND_READ_OWN)
        after = None
        if cursor is not None:
            try:
                after = decode_cursor(cursor)
            except ValueError as exc:
                raise InvalidInputError("Invalid cursor") from exc
        rows = await self.refunds.list_owned(
            principal.user_id, status=status, after=after, limit=limit + 1
        )
        page = rows[:limit]
        next_cursor = (
            encode_cursor(page[-1].created_at, page[-1].id)
            if len(rows) > limit
            else None
        )
        payouts = await self.payouts.map_for_refunds([r.id for r in page])
        return RefundPage(
            items=[self._read(r, payouts.get(r.id)) for r in page],
            next_cursor=next_cursor,
        )

    async def timeline(
        self, principal: Principal, refund_id: uuid.UUID
    ) -> Sequence[RefundEventRead]:
        """Public events only (internal types and payloads never reach customers)."""
        require_permission(principal, Perm.REFUND_READ_OWN)
        if await self.refunds.get_owned(refund_id, principal.user_id) is None:
            raise NotFoundError("Refund")
        events = await self.event_reads.list_for_refund(refund_id)
        return [
            RefundEventRead(
                type=e.type, actor_type=e.actor_type, created_at=e.created_at
            )
            for e in events
            if e.type in PUBLIC_EVENT_TYPES
        ]

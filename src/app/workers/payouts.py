"""Payout worker (spec 05): executes approved refunds from the outbox.

One transaction per outbox event, holding `FOR UPDATE SKIP LOCKED` on it, so two
workers never process the same event. A crash before commit rolls everything back
and the next run repeats the payment with the same idempotency key (the gateway
and the ledger make that a no-op), so nothing is paid or credited twice.

Run with `uv run payouts-worker` (`--once` drains the due events and exits).
"""

import argparse
import asyncio
import logging
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.domain.enums import (
    ActorType,
    OutboxStatus,
    PayoutStatus,
    PurchaseStatus,
    RefundMethod,
    RefundStatus,
)
from app.domain.state_machine import transition
from app.models.outbox import OutboxEvent
from app.models.purchase import Purchase
from app.models.refund import CreditLedgerEntry, RefundPayout, RefundRequest
from app.repositories.credit_ledger import CreditLedgerRepository
from app.repositories.outbox import TOPIC_REFUND_PAYOUT, OutboxRepository
from app.repositories.payout import RefundPayoutRepository
from app.repositories.supervisor_refund import SupervisorRefundRepository
from app.services.refund_event_service import EventActor, RefundEventService
from app.workers.gateway import (
    GatewayError,
    PaymentGateway,
    PermanentGatewayError,
    SimulatedGateway,
)

log = logging.getLogger("payouts-worker")

SYSTEM = EventActor(ActorType.SYSTEM)
EVENT_PROCESSING = "REFUND_PROCESSING"
EVENT_COMPLETED = "REFUND_COMPLETED"
EVENT_FAILED = "REFUND_FAILED"
# Internal (not in PUBLIC_EVENT_TYPES): one failed attempt that will be retried.
EVENT_PAYOUT_ATTEMPT_FAILED = "PAYOUT_ATTEMPT_FAILED"


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    max_attempts: int = 3
    backoff_base: timedelta = timedelta(seconds=30)

    def delay(self, attempts: int) -> timedelta:
        """Backoff after the `attempts`-th failure: base * 2^(attempts - 1)."""
        return self.backoff_base * float(2 ** (attempts - 1))


class PayoutWorker:
    def __init__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
        gateway: PaymentGateway,
        *,
        clock: Callable[[], datetime] = utc_now,
        config: WorkerConfig | None = None,
    ) -> None:
        self.session_maker = session_maker
        self.gateway = gateway
        self.clock = clock
        self.config = config or WorkerConfig()

    async def run_once(self) -> int:
        """Process every event that is due now; returns how many were handled."""
        handled = 0
        while await self.process_next():
            handled += 1
        return handled

    async def process_next(self) -> bool:
        """Claim and handle one due event in its own transaction."""
        async with self.session_maker() as session:
            try:
                event = await OutboxRepository(session).claim_next(
                    TOPIC_REFUND_PAYOUT, self.clock()
                )
                if event is None:
                    await session.rollback()
                    return False
                await self._handle(session, event)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        return True

    # ----------------------------------------------------------------- handling

    async def _handle(self, session: AsyncSession, event: OutboxEvent) -> None:
        refunds = SupervisorRefundRepository(session)
        payouts = RefundPayoutRepository(session)
        events = RefundEventService(session)
        now = self.clock()

        refund = await refunds.get_for_update(_refund_id(event))
        payout = (
            await payouts.get_by_refund(refund.id, for_update=True) if refund else None
        )
        if refund is None or payout is None:
            event.status = OutboxStatus.DEAD
            event.updated_at = now
            log.error("outbox %s points to a missing refund or payout", event.id)
            return
        if refund.status not in (RefundStatus.APPROVED, RefundStatus.PROCESSING):
            # Already completed (double delivery) or out of the flow: nothing to do.
            event.status = OutboxStatus.DONE
            event.updated_at = now
            return

        if refund.status is RefundStatus.APPROVED:
            refund.status = transition(
                refund.status, RefundStatus.PROCESSING, ActorType.SYSTEM
            )
            refund.version += 1
            refund.updated_at = now
            await session.flush()
            await events.append(
                refund.id,
                EVENT_PROCESSING,
                SYSTEM,
                {"from": "APPROVED", "to": "PROCESSING"},
            )

        error: str | None = None
        permanent = False
        try:
            provider_ref = await self.gateway.pay(
                payout.idempotency_key,
                payout.method.value,
                payout.amount,
                refund.currency,
            )
            # Effects of BR-23 are atomic: a savepoint undoes them all if one fails.
            try:
                async with session.begin_nested():
                    await self._complete(
                        session, event, refund, payout, provider_ref, events
                    )
                return
            except Exception as exc:  # noqa: BLE001 - recorded, then retried
                log.exception("effects of refund %s failed", refund.id)
                error = f"effects failed: {exc}"[:500]
                await session.refresh(refund)
                await session.refresh(payout)
                await session.refresh(event)
        except PermanentGatewayError as exc:
            error, permanent = str(exc)[:500], True
        except GatewayError as exc:
            error = str(exc)[:500]
        await self._record_failure(
            session, event, refund, payout, events, error or "unknown", permanent
        )

    async def _complete(
        self,
        session: AsyncSession,
        event: OutboxEvent,
        refund: RefundRequest,
        payout: RefundPayout,
        provider_ref: str,
        events: RefundEventService,
    ) -> None:
        now = self.clock()
        purchase = await session.get(Purchase, refund.purchase_id, with_for_update=True)
        assert purchase is not None  # FK
        purchase.status = PurchaseStatus.REFUNDED
        purchase.access_revoked_at = now
        purchase.updated_at = now

        credited = False
        if payout.method is RefundMethod.PLATFORM_CREDITS:
            ledger = CreditLedgerRepository(session)
            if await ledger.get_for_refund(refund.id) is None:
                await ledger.add(
                    CreditLedgerEntry(
                        customer_id=refund.customer_id,
                        delta=payout.amount,
                        refund_id=refund.id,
                    )
                )
                credited = True

        payout.status = PayoutStatus.SUCCEEDED
        payout.provider_ref = provider_ref
        payout.attempts += 1
        payout.last_error = None
        payout.updated_at = now
        refund.status = transition(
            refund.status, RefundStatus.COMPLETED, ActorType.SYSTEM
        )
        refund.version += 1
        refund.updated_at = now
        event.status = OutboxStatus.DONE
        event.updated_at = now
        await session.flush()
        await events.append(
            refund.id,
            EVENT_COMPLETED,
            SYSTEM,
            {
                "from": "PROCESSING",
                "to": "COMPLETED",
                "provider_ref": provider_ref,
                "credited": credited,
            },
        )

    async def _record_failure(
        self,
        session: AsyncSession,
        event: OutboxEvent,
        refund: RefundRequest,
        payout: RefundPayout,
        events: RefundEventService,
        error: str,
        permanent: bool,
    ) -> None:
        now = self.clock()
        payout.attempts += 1
        payout.last_error = error
        payout.updated_at = now
        if permanent or payout.attempts >= self.config.max_attempts:
            payout.status = PayoutStatus.FAILED
            refund.status = transition(
                refund.status, RefundStatus.FAILED, ActorType.SYSTEM
            )
            refund.version += 1
            refund.updated_at = now
            event.status = OutboxStatus.DEAD  # dead-letter
            event.updated_at = now
            await session.flush()
            await events.append(
                refund.id,
                EVENT_FAILED,
                SYSTEM,
                {"attempts": payout.attempts, "error": error},
            )
            return
        event.available_at = now + self.config.delay(payout.attempts)
        event.updated_at = now
        await session.flush()
        await events.append(
            refund.id,
            EVENT_PAYOUT_ATTEMPT_FAILED,
            SYSTEM,
            {
                "attempt": payout.attempts,
                "error": error,
                "retry_at": event.available_at.isoformat(),
            },
        )


def _refund_id(event: OutboxEvent) -> uuid.UUID:
    return uuid.UUID(str(event.payload["refund_id"]))


# ------------------------------------------------------------------------ CLI


def build_worker() -> PayoutWorker:
    settings = get_settings()
    return PayoutWorker(
        get_sessionmaker(),
        SimulatedGateway(fail_rate=settings.payout_fail_rate, seed=None),
        config=WorkerConfig(
            max_attempts=settings.payout_max_attempts,
            backoff_base=timedelta(seconds=settings.payout_backoff_base_seconds),
        ),
    )


async def _serve(worker: PayoutWorker, *, once: bool, poll: float) -> None:
    while True:
        handled = await worker.run_once()
        if once:
            log.info("handled %d event(s)", handled)
            return
        if not handled:
            await asyncio.sleep(poll)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Refund payout worker")
    parser.add_argument("--once", action="store_true", help="drain due events and exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=get_settings().log_level)
    # psycopg async cannot use Windows' default ProactorEventLoop.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=factory) as runner:
        try:
            runner.run(
                _serve(
                    build_worker(),
                    once=args.once,
                    poll=get_settings().payout_poll_seconds,
                )
            )
        except KeyboardInterrupt:  # pragma: no cover
            log.info("stopped")


if __name__ == "__main__":
    main()

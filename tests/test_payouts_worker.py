"""Outbox + payout worker (spec 05)."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import RoleName
from app.core.security import Principal
from app.domain.enums import (
    OutboxStatus,
    PayoutStatus,
    PurchaseStatus,
    RefundMethod,
    RefundStatus,
)
from app.models.outbox import OutboxEvent
from app.models.purchase import Purchase
from app.models.refund import (
    CreditLedgerEntry,
    RefundEvent,
    RefundPayout,
    RefundRequest,
)
from app.repositories.credit_ledger import CreditLedgerRepository
from app.repositories.outbox import OutboxRepository
from app.repositories.user import UserRepository
from app.services.supervisor_refund_service import SupervisorRefundService
from app.workers.gateway import SimulatedGateway
from app.workers.payouts import PayoutWorker, WorkerConfig
from conftest import API, Actor, MakePurchase, MakeRefund, MakeUser, PurchaseRef

Maker = async_sessionmaker[AsyncSession]
SUP = f"{API}/supervisor"
BASE = timedelta(seconds=10)


class Clock:
    """A settable clock, starting just after the approvals made by the API."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC) + timedelta(seconds=5)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def make_worker(
    maker: Maker, gateway: SimulatedGateway, clock: Clock | None = None
) -> PayoutWorker:
    return PayoutWorker(
        maker,
        gateway,
        clock=clock or Clock(),
        config=WorkerConfig(max_attempts=3, backoff_base=BASE),
    )


async def approved_refund(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    *,
    method: RefundMethod = RefundMethod.ORIGINAL_PAYMENT,
    customer: Actor | None = None,
    supervisor: Actor | None = None,
) -> tuple[uuid.UUID, Actor, Actor, PurchaseRef]:
    """A pending refund approved through the API (outbox written by `approve`)."""
    ann = customer or await make_user()
    sup = supervisor or await make_user(RoleName.SUPERVISOR)
    purchase = await make_purchase(ann)
    rid = await make_refund(purchase, method=method)
    res = await client.post(
        f"{SUP}/refunds/{rid}/approve", headers={**sup.headers, "If-Match": "1"}
    )
    assert res.status_code == 200, res.text
    return rid, ann, sup, purchase


async def refund_row(maker: Maker, rid: uuid.UUID) -> RefundRequest:
    async with maker() as session:
        row = await session.get(RefundRequest, rid)
        assert row is not None
        return row


async def payout_row(maker: Maker, rid: uuid.UUID) -> RefundPayout:
    async with maker() as session:
        row = await session.scalar(
            select(RefundPayout).where(RefundPayout.refund_id == rid)
        )
        assert row is not None
        return row


async def outbox_rows(maker: Maker) -> list[OutboxEvent]:
    async with maker() as session:
        return list(
            await session.scalars(select(OutboxEvent).order_by(OutboxEvent.created_at))
        )


async def ledger_entries(maker: Maker, rid: uuid.UUID) -> int:
    async with maker() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(CreditLedgerEntry)
                .where(CreditLedgerEntry.refund_id == rid)
            )
            or 0
        )


async def event_types(maker: Maker, rid: uuid.UUID) -> list[str]:
    async with maker() as session:
        rows = await session.scalars(
            select(RefundEvent.type)
            .where(RefundEvent.refund_id == rid)
            .order_by(RefundEvent.created_at)
        )
        return list(rows)


# ------------------------------------------------------------------ approval


async def test_approve_writes_payout_and_outbox_event(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    rid, _, _, _ = await approved_refund(client, make_user, make_purchase, make_refund)
    (event,) = await outbox_rows(session_maker)
    assert event.topic == "refund.payout"
    assert event.payload["refund_id"] == str(rid)
    assert event.payload["trace_id"]  # spec 13: the approving request's trace id
    assert event.status is OutboxStatus.PENDING
    payout = await payout_row(session_maker, rid)
    assert payout.status is PayoutStatus.PENDING
    assert payout.idempotency_key == str(rid)
    assert payout.amount == Decimal("49.99")
    assert (await refund_row(session_maker, rid)).status is RefundStatus.APPROVED


async def test_reject_writes_no_outbox(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    rid = await make_refund(await make_purchase(ann))
    res = await client.post(
        f"{SUP}/refunds/{rid}/reject",
        headers={**sup.headers, "If-Match": "1"},
        json={"note": "Outside what the policy allows."},
    )
    assert res.status_code == 200
    assert await outbox_rows(session_maker) == []


async def test_approve_is_atomic_with_the_outbox(
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error after the UPDATE leaves neither the new state nor an event."""
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    rid = await make_refund(await make_purchase(ann))

    async def boom(self: OutboxRepository, entity: OutboxEvent) -> OutboxEvent:
        raise RuntimeError("outbox down")

    monkeypatch.setattr(OutboxRepository, "add", boom)
    async with session_maker() as session:
        principal = await UserRepository(session).get_principal(sup.id)
        assert isinstance(principal, Principal)
        service = SupervisorRefundService(session)
        with pytest.raises(RuntimeError):
            await service.approve(principal, rid, if_match=1)
        await session.rollback()

    assert (await refund_row(session_maker, rid)).status is (
        RefundStatus.PENDING_APPROVAL
    )
    assert await outbox_rows(session_maker) == []
    async with session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(RefundPayout)) == 0
    assert "REFUND_APPROVED" not in await event_types(session_maker, rid)


# ------------------------------------------------------------------- success


async def test_success_completes_and_refunds_the_purchase(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    rid, ann, _, purchase = await approved_refund(
        client, make_user, make_purchase, make_refund
    )
    gateway = SimulatedGateway()
    assert await make_worker(session_maker, gateway).run_once() == 1

    refund = await refund_row(session_maker, rid)
    assert refund.status is RefundStatus.COMPLETED
    payout = await payout_row(session_maker, rid)
    assert payout.status is PayoutStatus.SUCCEEDED
    assert payout.provider_ref and payout.attempts == 1
    async with session_maker() as session:
        row = await session.get(Purchase, purchase.id)
        assert row is not None
        assert row.status is PurchaseStatus.REFUNDED
        assert row.access_revoked_at is not None
    (event,) = await outbox_rows(session_maker)
    assert event.status is OutboxStatus.DONE
    # ORIGINAL_PAYMENT does not touch the credit balance.
    assert await ledger_entries(session_maker, rid) == 0
    me = await client.get(f"{API}/me", headers=ann.headers)
    assert Decimal(str(me.json()["credit_balance"])) == 0

    types = await event_types(session_maker, rid)
    assert types[-2:] == ["REFUND_PROCESSING", "REFUND_COMPLETED"]
    async with session_maker() as session:
        actors = set(
            await session.scalars(
                select(RefundEvent.actor_type).where(
                    RefundEvent.refund_id == rid,
                    RefundEvent.type.in_(["REFUND_PROCESSING", "REFUND_COMPLETED"]),
                )
            )
        )
    assert {a.value for a in actors} == {"SYSTEM"}


async def test_platform_credits_raise_the_balance_only_for_that_method(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    rid, ann, _, _ = await approved_refund(
        client,
        make_user,
        make_purchase,
        make_refund,
        method=RefundMethod.PLATFORM_CREDITS,
    )
    await make_worker(session_maker, SimulatedGateway()).run_once()
    assert await ledger_entries(session_maker, rid) == 1
    me = await client.get(f"{API}/me", headers=ann.headers)
    assert Decimal(str(me.json()["credit_balance"])) == Decimal("49.99")


async def test_customer_detail_shows_payout_status(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    rid, ann, sup, _ = await approved_refund(
        client, make_user, make_purchase, make_refund
    )
    body = (await client.get(f"{API}/refunds/{rid}", headers=ann.headers)).json()
    assert body["status"] == "APPROVED"
    assert body["payout"]["status"] == "PENDING"
    assert "last_error" not in body["payout"]

    await make_worker(session_maker, SimulatedGateway()).run_once()
    body = (await client.get(f"{API}/refunds/{rid}", headers=ann.headers)).json()
    assert body["status"] == "COMPLETED"
    assert body["payout"]["status"] == "SUCCEEDED"
    page = (await client.get(f"{API}/refunds", headers=ann.headers)).json()
    assert page["items"][0]["payout"]["status"] == "SUCCEEDED"
    detail = (await client.get(f"{SUP}/refunds/{rid}", headers=sup.headers)).json()
    assert detail["payout"]["attempts"] == 1


async def test_completed_purchase_is_no_longer_eligible(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    rid, ann, _, purchase = await approved_refund(
        client, make_user, make_purchase, make_refund
    )
    await make_worker(session_maker, SimulatedGateway()).run_once()
    assert (await refund_row(session_maker, rid)).status is RefundStatus.COMPLETED
    res = await client.get(
        f"{API}/purchases/{purchase.id}/refund-eligibility", headers=ann.headers
    )
    assert res.status_code == 200
    body = res.json()
    assert body["eligible"] is False
    assert "ALREADY_REFUNDED" in body["reason_codes"]


# ------------------------------------------------------ retries and dead-letter


async def test_transient_failures_back_off_then_fail_and_manual_retry_completes(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    caplog: pytest.LogCaptureFixture,
) -> None:
    rid, _, sup, _ = await approved_refund(
        client, make_user, make_purchase, make_refund
    )
    clock = Clock()
    gateway = SimulatedGateway(failures=3)
    worker = make_worker(session_maker, gateway, clock)
    t0 = clock.now

    # attempt 1 fails: PROCESSING, retried after base * 2^0
    assert await worker.run_once() == 1
    refund = await refund_row(session_maker, rid)
    assert refund.status is RefundStatus.PROCESSING
    (event,) = await outbox_rows(session_maker)
    assert event.status is OutboxStatus.PENDING
    assert event.available_at == t0 + BASE
    payout = await payout_row(session_maker, rid)
    assert payout.attempts == 1 and payout.last_error

    # not due yet: nothing happens
    assert await worker.run_once() == 0
    assert (await payout_row(session_maker, rid)).attempts == 1

    # attempt 2 fails: next retry after base * 2^1
    clock.advance(BASE)
    assert await worker.run_once() == 1
    (event,) = await outbox_rows(session_maker)
    assert event.available_at == clock.now + 2 * BASE

    # attempt 3 fails: dead-letter and FAILED
    clock.advance(2 * BASE)
    assert await worker.run_once() == 1
    refund = await refund_row(session_maker, rid)
    assert refund.status is RefundStatus.FAILED
    payout = await payout_row(session_maker, rid)
    assert payout.status is PayoutStatus.FAILED and payout.attempts == 3
    (event,) = await outbox_rows(session_maker)
    assert event.status is OutboxStatus.DEAD
    # Spec 12: dead-letter raises one structured, alertable log line.
    alarms = [r for r in caplog.records if "event=payout_dead" in r.getMessage()]
    assert len(alarms) == 1 and alarms[0].levelno == logging.ERROR
    assert f"refund_id={rid}" in alarms[0].getMessage()
    assert "attempts=3" in alarms[0].getMessage()
    assert gateway.payments_made == 0
    assert (await event_types(session_maker, rid))[-1] == "REFUND_FAILED"

    # the supervisor retries: new outbox event, the gateway recovers
    res = await client.post(f"{SUP}/refunds/{rid}/retry-payout", headers=sup.headers)
    assert res.status_code == 202
    assert res.json()["status"] == "PROCESSING"
    assert len(await outbox_rows(session_maker)) == 2
    clock.advance(timedelta(seconds=1))
    assert await worker.run_once() == 1
    assert (await refund_row(session_maker, rid)).status is RefundStatus.COMPLETED
    assert (await payout_row(session_maker, rid)).status is PayoutStatus.SUCCEEDED
    assert gateway.payments_made == 1


async def test_retry_payout_rules(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    rid, ann, sup, _ = await approved_refund(
        client, make_user, make_purchase, make_refund
    )
    url = f"{SUP}/refunds/{rid}/retry-payout"
    # not FAILED (it is APPROVED): 409
    res = await client.post(url, headers=sup.headers)
    assert res.status_code == 409
    assert res.json()["code"] == "REFUND_NOT_FAILED"
    # customers lack refund:retry_payout
    assert (await client.post(url, headers=ann.headers)).status_code == 403
    # unknown refund
    missing = f"{SUP}/refunds/{uuid.uuid4()}/retry-payout"
    assert (await client.post(missing, headers=sup.headers)).status_code == 404


async def test_permanent_gateway_error_fails_without_retries(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    from app.workers.gateway import PermanentGatewayError

    class Declined(SimulatedGateway):
        async def pay(self, *args: Any, **kwargs: Any) -> str:
            raise PermanentGatewayError("account closed")

    rid, _, _, _ = await approved_refund(client, make_user, make_purchase, make_refund)
    await make_worker(session_maker, Declined()).run_once()
    assert (await refund_row(session_maker, rid)).status is RefundStatus.FAILED
    payout = await payout_row(session_maker, rid)
    assert payout.attempts == 1 and payout.last_error == "account closed"


# -------------------------------------------------------------- idempotency


async def test_gateway_is_idempotent_per_key() -> None:
    gateway = SimulatedGateway()
    first = await gateway.pay("k1", "ORIGINAL_PAYMENT", Decimal("10"), "USD")
    again = await gateway.pay("k1", "ORIGINAL_PAYMENT", Decimal("10"), "USD")
    other = await gateway.pay("k2", "ORIGINAL_PAYMENT", Decimal("10"), "USD")
    assert first == again != other
    assert gateway.payments_made == 2


async def test_running_the_same_event_twice_pays_and_credits_once(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    rid, ann, _, _ = await approved_refund(
        client,
        make_user,
        make_purchase,
        make_refund,
        method=RefundMethod.PLATFORM_CREDITS,
    )
    gateway = SimulatedGateway()
    worker = make_worker(session_maker, gateway)
    await worker.run_once()

    # redeliver the very same event
    async with session_maker() as session:
        event = await session.scalar(select(OutboxEvent))
        assert event is not None
        event.status = OutboxStatus.PENDING
        await session.commit()
    assert await worker.run_once() == 1

    assert gateway.payments_made == 1
    assert await ledger_entries(session_maker, rid) == 1
    assert (await refund_row(session_maker, rid)).status is RefundStatus.COMPLETED
    me = await client.get(f"{API}/me", headers=ann.headers)
    assert Decimal(str(me.json()["credit_balance"])) == Decimal("49.99")
    assert (await outbox_rows(session_maker))[0].status is OutboxStatus.DONE


async def test_a_crash_after_paying_repeats_safely(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paid at the gateway but the local commit never happened: the re-run reuses
    the idempotency key, so the gateway does not pay again."""
    rid, _, _, _ = await approved_refund(
        client,
        make_user,
        make_purchase,
        make_refund,
        method=RefundMethod.PLATFORM_CREDITS,
    )
    gateway = SimulatedGateway()
    worker = make_worker(session_maker, gateway)

    async def crash(self: AsyncSession) -> None:
        raise SystemError("killed")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", crash)
        with pytest.raises(SystemError):
            await worker.process_next()
    assert gateway.payments_made == 1
    assert (await refund_row(session_maker, rid)).status is RefundStatus.APPROVED

    await worker.run_once()
    assert gateway.payments_made == 1  # same key: not paid twice
    assert await ledger_entries(session_maker, rid) == 1
    assert (await refund_row(session_maker, rid)).status is RefundStatus.COMPLETED


# ------------------------------------------------------------------ atomicity


async def test_effects_are_atomic_if_the_ledger_insert_fails(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rid, _, _, purchase = await approved_refund(
        client,
        make_user,
        make_purchase,
        make_refund,
        method=RefundMethod.PLATFORM_CREDITS,
    )
    clock = Clock()
    gateway = SimulatedGateway()
    worker = make_worker(session_maker, gateway, clock)

    async def boom(
        self: CreditLedgerRepository, entity: CreditLedgerEntry
    ) -> CreditLedgerEntry:
        raise RuntimeError("ledger down")

    with monkeypatch.context() as patch:
        patch.setattr(CreditLedgerRepository, "add", boom)
        assert await worker.run_once() == 1

    async with session_maker() as session:
        row = await session.get(Purchase, purchase.id)
        assert row is not None
        assert row.status is PurchaseStatus.ACTIVE
        assert row.access_revoked_at is None
    assert await ledger_entries(session_maker, rid) == 0
    refund = await refund_row(session_maker, rid)
    assert refund.status is RefundStatus.PROCESSING
    payout = await payout_row(session_maker, rid)
    assert payout.status is PayoutStatus.PENDING
    assert payout.attempts == 1 and "ledger down" in (payout.last_error or "")
    (event,) = await outbox_rows(session_maker)
    assert event.status is OutboxStatus.PENDING

    # once the ledger works again the retry completes everything
    clock.advance(BASE)
    assert await worker.run_once() == 1
    assert (await refund_row(session_maker, rid)).status is RefundStatus.COMPLETED
    assert await ledger_entries(session_maker, rid) == 1
    assert gateway.payments_made == 1


# ---------------------------------------------------------------- concurrency


async def test_concurrent_workers_process_each_event_once(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    class SlowGateway(SimulatedGateway):
        async def pay(self, *args: Any, **kwargs: Any) -> str:
            await asyncio.sleep(0.2)  # keep the row lock long enough to overlap
            return await super().pay(*args, **kwargs)

    sup = await make_user(RoleName.SUPERVISOR)
    ids = []
    for _ in range(4):
        rid, _, _, _ = await approved_refund(
            client, make_user, make_purchase, make_refund, supervisor=sup
        )
        ids.append(rid)

    gateway = SlowGateway()
    clock = Clock()
    handled = await asyncio.gather(
        *(make_worker(session_maker, gateway, clock).run_once() for _ in range(3))
    )
    assert sum(handled) == 4
    assert gateway.payments_made == 4
    for rid in ids:
        assert (await refund_row(session_maker, rid)).status is RefundStatus.COMPLETED
        assert (await payout_row(session_maker, rid)).attempts == 1

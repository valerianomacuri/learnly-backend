"""Guarantees enforced by the database itself, not by application code."""

import uuid

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import ActorType, RefundStatus
from app.models.refund import RefundEvent, RefundRequest
from app.services.refund_event_service import EventActor, RefundEventService
from conftest import MakePurchase, MakeRefund, MakeUser

Maker = async_sessionmaker[AsyncSession]


async def _event(maker: Maker, refund_id: uuid.UUID) -> uuid.UUID:
    async with maker() as session:
        event = await RefundEventService(session).append(
            refund_id, "REFUND_CREATED", EventActor(ActorType.SYSTEM), {"k": "v"}
        )
        await session.commit()
        return event.id


async def test_refund_events_reject_update(
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    customer = await make_user()
    refund_id = await make_refund(await make_purchase(customer))
    event_id = await _event(session_maker, refund_id)

    async with session_maker() as session:
        with pytest.raises(IntegrityError, match="append-only"):
            await session.execute(
                update(RefundEvent)
                .where(RefundEvent.id == event_id)
                .values(type="TAMPERED")
            )


async def test_refund_events_reject_delete(
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    customer = await make_user()
    refund_id = await make_refund(await make_purchase(customer))
    event_id = await _event(session_maker, refund_id)

    async with session_maker() as session:
        with pytest.raises(IntegrityError, match="append-only"):
            await session.execute(delete(RefundEvent).where(RefundEvent.id == event_id))

    async with session_maker() as session:  # the row is still there, unchanged
        stored = await session.get(RefundEvent, event_id)
        assert stored is not None and stored.type == "REFUND_CREATED"


async def test_refund_events_keep_their_order_within_one_transaction(
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    customer = await make_user()
    refund_id = await make_refund(await make_purchase(customer))
    async with session_maker() as session:
        service = RefundEventService(session)
        for name in ("A", "B", "C"):
            await service.append(refund_id, name, EventActor(ActorType.SYSTEM))
        await session.commit()
    async with session_maker() as session:
        events = await RefundEventService(session).repo.list_for_refund(refund_id)
    assert [e.type for e in events] == ["A", "B", "C"]


async def test_only_one_active_refund_per_purchase(
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    customer = await make_user()
    purchase = await make_purchase(customer)
    await make_refund(purchase, status=RefundStatus.PENDING_APPROVAL)

    for status in (
        RefundStatus.PENDING_APPROVAL,
        RefundStatus.APPROVED,
        RefundStatus.PROCESSING,
    ):
        with pytest.raises(IntegrityError, match="uq_refund_requests_active_purchase"):
            await make_refund(purchase, status=status)


async def test_a_new_request_is_allowed_after_cancel_or_rejection(
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    # The index only constrains active states; BR-05 (a rejected purchase cannot
    # be requested again) is business logic for the engine (spec 02), not a
    # database rule.
    customer = await make_user()
    purchase = await make_purchase(customer)
    cancelled = await make_refund(purchase, status=RefundStatus.CANCELLED)
    new = await make_refund(purchase, status=RefundStatus.PENDING_APPROVAL)
    assert new != cancelled

    async with session_maker() as session:
        await session.execute(
            update(RefundRequest)
            .where(RefundRequest.id == new)
            .values(status=RefundStatus.CANCELLED)
        )
        await session.commit()
    await make_refund(purchase, status=RefundStatus.PENDING_APPROVAL)


async def test_idempotency_key_is_unique_per_customer(
    make_user: MakeUser, make_purchase: MakePurchase, make_refund: MakeRefund
) -> None:
    ann, bob = await make_user(), await make_user()
    first = await make_purchase(ann)
    await make_refund(first, status=RefundStatus.CANCELLED, idempotency_key="k1")
    with pytest.raises(IntegrityError, match="uq_refund_requests_customer_id"):
        await make_refund(first, status=RefundStatus.CANCELLED, idempotency_key="k1")
    # another customer may reuse the key
    await make_refund(await make_purchase(bob), idempotency_key="k1")


async def test_reason_text_length_is_checked(
    make_user: MakeUser, make_purchase: MakePurchase, make_refund: MakeRefund
) -> None:
    purchase = await make_purchase(await make_user())
    with pytest.raises(IntegrityError, match="reason_text_length"):
        await make_refund(purchase, reason_text="too short")
    with pytest.raises(IntegrityError, match="reason_text_length"):
        await make_refund(purchase, reason_text="x" * 1001)
    await make_refund(purchase, status=RefundStatus.CANCELLED, reason_text="x" * 20)
    await make_refund(purchase, reason_text="x" * 1000)


async def test_enum_columns_reject_unknown_values(
    session_maker: Maker, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    purchase = await make_purchase(await make_user())
    async with session_maker() as session:
        with pytest.raises(IntegrityError, match="purchase_status"):
            await session.execute(
                text("UPDATE purchases SET status = 'BOGUS' WHERE id = :id"),
                {"id": purchase.id},
            )

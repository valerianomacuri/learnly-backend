"""Spec 06: SSE stream of refund changes (LISTEN/NOTIFY) and CORS."""

import asyncio
import json
import os

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import ActorType
from app.services.refund_event_service import EventActor, RefundEventService
from app.services.refund_stream import HEARTBEAT, refund_event_stream
from conftest import API, MakePurchase, MakeRefund, MakeUser

URL = f"{API}/supervisor/refunds/stream"


async def next_frame(stream: object) -> str:
    return await asyncio.wait_for(stream.__anext__(), 5)  # type: ignore[attr-defined]


async def test_only_supervisors_may_open_the_stream(
    client: AsyncClient, make_user: MakeUser
) -> None:
    ann = await make_user()
    assert (await client.get(URL)).status_code == 401
    assert (await client.get(URL, headers=ann.headers)).status_code == 403


async def test_commit_emits_event_and_rollback_does_not(
    session_maker: async_sessionmaker[AsyncSession],
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    refund_id = await make_refund(await make_purchase(ann))
    stream = refund_event_stream(os.environ["DATABASE_URL"], heartbeat_seconds=0.3)
    try:
        assert await next_frame(stream) == HEARTBEAT  # connected and listening

        async with session_maker() as session:  # rolled back: nothing is emitted
            await RefundEventService(session).append(
                refund_id, "REFUND_APPROVED", EventActor(ActorType.SYSTEM)
            )
            await session.rollback()

        async with session_maker() as session:
            await RefundEventService(session).append(
                refund_id, "REFUND_APPROVED", EventActor(ActorType.SYSTEM)
            )
            await session.commit()

        frame = ""
        while not frame.startswith("event:"):  # skip heartbeats
            frame = await next_frame(stream)
        head, data = frame.strip().split("\n")
        assert head == "event: refund.updated"
        payload = json.loads(data.removeprefix("data: "))
        assert payload == {
            "id": str(refund_id),
            "status": "PENDING_APPROVAL",
            "version": 1,
        }
    finally:
        await stream.aclose()  # type: ignore[attr-defined]


async def test_created_event_name(
    session_maker: async_sessionmaker[AsyncSession],
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    refund_id = await make_refund(await make_purchase(ann))
    stream = refund_event_stream(os.environ["DATABASE_URL"], heartbeat_seconds=0.3)
    try:
        await next_frame(stream)
        async with session_maker() as session:
            await RefundEventService(session).append(
                refund_id, "REFUND_CREATED", EventActor(ActorType.CUSTOMER, ann.id)
            )
            await session.commit()
        frame = ""
        while not frame.startswith("event:"):
            frame = await next_frame(stream)
        assert frame.startswith("event: refund.created\n")
    finally:
        await stream.aclose()  # type: ignore[attr-defined]


async def test_idle_stream_sends_heartbeats() -> None:
    stream = refund_event_stream(os.environ["DATABASE_URL"], heartbeat_seconds=0.2)
    try:
        assert await next_frame(stream) == HEARTBEAT
        assert await next_frame(stream) == HEARTBEAT
    finally:
        await stream.aclose()  # type: ignore[attr-defined]


async def test_cors_preflight_allows_the_dev_origin(client: AsyncClient) -> None:
    response = await client.options(
        f"{API}/refunds",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,idempotency-key,if-match",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "idempotency-key" in allowed and "if-match" in allowed
    other = await client.options(
        f"{API}/refunds",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in other.headers

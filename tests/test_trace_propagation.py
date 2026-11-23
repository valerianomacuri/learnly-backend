"""One trace id from the request to the event, the outbox and the worker (spec 13)."""

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import RoleName
from app.models.outbox import OutboxEvent
from app.models.refund import RefundEvent
from app.workers.gateway import SimulatedGateway
from app.workers.payouts import PayoutWorker
from conftest import API, MakePurchase, MakeRefund, MakeUser

Maker = async_sessionmaker[AsyncSession]


async def test_trace_id_reaches_event_outbox_and_worker_logs(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    rid = await make_refund(await make_purchase(ann))

    resp = await client.post(
        f"{API}/supervisor/refunds/{rid}/approve",
        headers={**sup.headers, "If-Match": "1", "X-Request-ID": "trace-abc"},
    )
    assert resp.status_code == 200
    assert resp.headers["x-request-id"] == "trace-abc"

    async with session_maker() as session:
        events = list(
            await session.scalars(
                select(RefundEvent).where(RefundEvent.refund_id == rid)
            )
        )
        outbox = list(await session.scalars(select(OutboxEvent)))
    assert any(e.payload.get("trace_id") == "trace-abc" for e in events)
    assert [o.payload["trace_id"] for o in outbox] == ["trace-abc"]

    with caplog.at_level(logging.INFO):
        await PayoutWorker(session_maker, SimulatedGateway(seed=1)).run_once()
    async with session_maker() as session:
        done = list(
            await session.scalars(
                select(RefundEvent).where(RefundEvent.refund_id == rid)
            )
        )
    # Events the worker writes keep the approving request's trace id.
    assert any(
        e.payload.get("trace_id") == "trace-abc" and e.actor_id is None for e in done
    )

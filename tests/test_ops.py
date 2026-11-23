"""/readyz, /metrics and the business counters (spec 13)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from prometheus_client import REGISTRY
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.database import get_session
from app.core.permissions import RoleName
from app.main import app
from conftest import API, MakePurchase, MakeRefund, MakeUser


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_readyz_ok(client: AsyncClient) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["vector_store"] == "ok"


@pytest.mark.redis
async def test_readyz_with_redis(client: AsyncClient, redis_client: Redis) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["checks"]["redis"] == "ok"


async def test_readyz_503_when_redis_is_down(
    client: AsyncClient, broken_redis: Redis
) -> None:
    resp = await client.get("/readyz")
    assert resp.status_code == 503
    checks = resp.json()["checks"]
    assert checks["redis"] != "ok" and checks["database"] == "ok"


async def test_readyz_503_when_database_is_down() -> None:
    class Dead:
        async def execute(self, *_: Any, **__: Any) -> None:
            raise ConnectionError("db down")

    async def broken() -> AsyncIterator[Any]:
        yield Dead()

    app.dependency_overrides[get_session] = broken
    try:
        from httpx import ASGITransport

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            resp = await http.get("/readyz")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 503
    assert resp.json()["checks"]["database"].startswith("down")


async def test_readyz_503_when_vector_store_is_missing(
    client: AsyncClient, session_maker: Any
) -> None:
    from sqlalchemy import text

    async with session_maker() as session:
        await session.execute(
            text("ALTER TABLE policy_chunks RENAME TO policy_chunks_x")
        )
        await session.commit()
    try:
        resp = await client.get("/readyz")
    finally:
        async with session_maker() as session:
            await session.execute(
                text("ALTER TABLE policy_chunks_x RENAME TO policy_chunks")
            )
            await session.commit()
    assert resp.status_code == 503
    assert resp.json()["checks"]["vector_store"].startswith("down")


async def test_routes_are_not_in_openapi() -> None:
    paths = app.openapi()["paths"]
    assert "/readyz" not in paths and "/metrics" not in paths


async def test_metrics_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_resp = await client.get("/metrics")
    assert open_resp.status_code == 200
    assert "refund_requests_total" in open_resp.text

    monkeypatch.setenv("METRICS_TOKEN", "s3cret-metrics-token")
    get_settings.cache_clear()
    try:
        assert (await client.get("/metrics")).status_code == 401
        bad = await client.get("/metrics", headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401
        ok = await client.get(
            "/metrics", headers={"Authorization": "Bearer s3cret-metrics-token"}
        )
        assert ok.status_code == 200
    finally:
        monkeypatch.delenv("METRICS_TOKEN")
        get_settings.cache_clear()


async def test_decision_and_outbox_metrics(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    rid = await make_refund(await make_purchase(ann))
    before = sample("refund_decisions_total", decision="APPROVED")
    count_before = sample("refund_time_to_decision_seconds_count")

    resp = await client.post(
        f"{API}/supervisor/refunds/{rid}/approve",
        headers={**sup.headers, "If-Match": "1"},
    )
    assert resp.status_code == 200
    assert sample("refund_decisions_total", decision="APPROVED") == before + 1
    assert sample("refund_time_to_decision_seconds_count") == count_before + 1

    await client.get("/metrics")  # refreshes the DB-backed gauges
    assert sample("refund_outbox_pending") >= 1


async def test_creation_and_rejection_counters(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    from test_refunds_api import create, preview

    ann = await make_user()
    ok = await make_purchase(ann)
    late = await make_purchase(ann, days_ago=45)
    created = sample("refund_requests_total", channel="MANUAL_FORM")
    rejected = sample(
        "refund_eligibility_rejections_total", reason_code="OUTSIDE_REFUND_WINDOW"
    )

    token = (await preview(client, ann, ok.id)).json()["confirmation_token"]
    assert (await create(client, ann, ok.id, token)).status_code == 201
    assert (await create(client, ann, late.id, "whatever")).status_code == 422

    assert sample("refund_requests_total", channel="MANUAL_FORM") == created + 1
    assert (
        sample(
            "refund_eligibility_rejections_total", reason_code="OUTSIDE_REFUND_WINDOW"
        )
        == rejected + 1
    )

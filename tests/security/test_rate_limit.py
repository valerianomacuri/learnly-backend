"""Rate limits (spec 12): sliding window, per customer, 429 + Retry-After, outage policy."""

import asyncio
import sys
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))

from agent_helpers import Maker, make_runtime, scripted_llm  # noqa: E402

from app.agent.state import Intent  # noqa: E402
from app.api.deps import rate_limit  # noqa: E402
from app.core.permissions import RoleName  # noqa: E402
from app.core.rate_limit import SlidingWindowLimiter  # noqa: E402
from app.core.redis import ThreadLock  # noqa: E402
from app.main import app  # noqa: E402
from conftest import API, MakeUser  # noqa: E402

pytestmark = pytest.mark.redis


@pytest.fixture
def runtime(session_maker: Maker):  # type: ignore[no-untyped-def]
    app.state.agent_runtime = make_runtime(
        session_maker, scripted_llm([Intent.POLICY_QUESTION])
    )
    yield
    app.state.agent_runtime = None


async def test_window_blocks_then_reopens(redis_client: Redis) -> None:
    limiter = SlidingWindowLimiter(redis_client)
    for _ in range(3):
        assert (await limiter.hit("k", limit=3, window_s=0.5)).allowed
    blocked = await limiter.hit("k", limit=3, window_s=0.5)
    assert not blocked.allowed and blocked.retry_after == 1
    await asyncio.sleep(0.6)
    assert (await limiter.hit("k", limit=3, window_s=0.5)).allowed


async def test_keys_are_independent(redis_client: Redis) -> None:
    limiter = SlidingWindowLimiter(redis_client)
    assert (await limiter.hit("a", limit=1, window_s=60)).allowed
    assert not (await limiter.hit("a", limit=1, window_s=60)).allowed
    assert (await limiter.hit("b", limit=1, window_s=60)).allowed


async def test_chat_21st_message_in_a_minute_is_429(
    client: AsyncClient, make_user: MakeUser, redis_client: Redis, runtime: None
) -> None:
    ann = await make_user(RoleName.CUSTOMER)
    bob = await make_user(RoleName.CUSTOMER)
    url = f"{API}/chat/threads/{uuid.uuid4()}/messages"
    for _ in range(20):
        r = await client.post(url, headers=ann.headers, json={"content": "hola"})
        assert r.status_code == 404  # counted even though the thread is not hers
    over = await client.post(url, headers=ann.headers, json={"content": "hola"})
    assert over.status_code == 429
    assert int(over.headers["Retry-After"]) >= 1
    assert over.json()["code"] == "RATE_LIMITED"
    # `resume` shares the counter ...
    resume = await client.post(
        f"{API}/chat/threads/{uuid.uuid4()}/resume",
        headers=ann.headers,
        json={"interrupt_id": "x", "decision": "cancel"},
    )
    assert resume.status_code == 429
    # ... but it is per customer
    other = await client.post(url, headers=bob.headers, json={"content": "hola"})
    assert other.status_code == 404


async def test_sixth_refund_in_an_hour_is_429(
    client: AsyncClient, make_user: MakeUser, redis_client: Redis
) -> None:
    ann = await make_user(RoleName.CUSTOMER)
    for i in range(5):
        r = await client.post(
            f"{API}/refunds",
            headers={**ann.headers, "Idempotency-Key": f"k{i}"},
            json={},
        )
        assert r.status_code == 422
    over = await client.post(
        f"{API}/refunds", headers={**ann.headers, "Idempotency-Key": "k6"}, json={}
    )
    assert over.status_code == 429
    assert "Retry-After" in over.headers


async def test_no_redis_configured_disables_limits(
    client: AsyncClient, make_user: MakeUser
) -> None:
    app.state.redis = None
    ann = await make_user(RoleName.CUSTOMER)
    for i in range(7):
        r = await client.post(
            f"{API}/refunds",
            headers={**ann.headers, "Idempotency-Key": f"k{i}"},
            json={},
        )
        assert r.status_code == 422


async def test_redis_outage_fails_closed_for_writes(
    client: AsyncClient, make_user: MakeUser, broken_redis: Redis
) -> None:
    ann = await make_user(RoleName.CUSTOMER)
    r = await client.post(
        f"{API}/refunds", headers={**ann.headers, "Idempotency-Key": "k"}, json={}
    )
    assert r.status_code == 503
    assert r.json()["code"] == "RATE_LIMIT_UNAVAILABLE"


async def test_redis_outage_fails_open_for_reads(
    make_user: MakeUser, broken_redis: Redis
) -> None:
    ann = await make_user(RoleName.CUSTOMER)
    dep = rate_limit("probe", 1, 60, write=False)

    class _App:
        state = app.state

    class _Req:
        app = _App()

    class _Principal:
        user_id = ann.id

    # A read-style limit never blocks while Redis is down.
    assert await dep(_Req(), _Principal()) is None  # type: ignore[arg-type]


async def test_thread_lock_is_exclusive(redis_client: Redis) -> None:
    thread_id = uuid.uuid4()
    first, second = (
        ThreadLock(redis_client, thread_id),
        ThreadLock(redis_client, thread_id),
    )
    assert await first.acquire()
    assert not await second.acquire()
    await first.release()
    assert await second.acquire()
    await second.release()

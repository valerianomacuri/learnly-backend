import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Must be set before `app` modules read settings.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret")
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/customer_support_test",
    ),
)

from app.core.database import get_session  # noqa: E402
from app.core.passwords import hash_password  # noqa: E402
from app.core.permissions import RoleName  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.domain.enums import (  # noqa: E402
    Channel,
    PaymentMethod,
    PurchaseStatus,
    PurchaseType,
    ReasonCategory,
    RefundMethod,
    RefundStatus,
)
from app.main import app  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.course import Course  # noqa: E402
from app.models.purchase import CourseProgress, Purchase  # noqa: E402
from app.models.refund import RefundRequest  # noqa: E402
from app.models.user import User  # noqa: E402
from app.policy.load import load_policies  # noqa: E402
from app.rag.embeddings import FakeEmbeddings  # noqa: E402
from app.rag.ingest import IngestReport, ingest  # noqa: E402
from app.services.rbac import ensure_rbac  # noqa: E402

API = "/api/v1"
POLICIES_DIR = Path(__file__).resolve().parents[1] / "policies"
PASSWORD = "password-123"
# Argon2 is deliberately slow: hash once per test session, not once per user.
PASSWORD_HASH = hash_password(PASSWORD)


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
    # psycopg async can't use Windows' default ProactorEventLoop.
    return {"selector": asyncio.SelectorEventLoop}


@dataclass
class Actor:
    """A user stored in the test database plus ready-to-use auth headers."""

    id: uuid.UUID
    headers: dict[str, str]
    email: str = ""
    password: str = PASSWORD


MakeUser = Callable[..., Awaitable[Actor]]


@pytest.fixture
async def session_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Session factory on a fresh schema in the test database (needs Postgres)."""
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        # Every DB test runs with the real active policy, like a started app.
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await load_policies(session, POLICIES_DIR)
            await session.commit()
    except OperationalError as exc:  # pragma: no cover
        await engine.dispose()
        pytest.skip(f"Postgres unavailable: {exc}")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def embedder() -> FakeEmbeddings:
    return FakeEmbeddings()


@pytest.fixture
async def ingested(
    session_maker: async_sessionmaker[AsyncSession], embedder: FakeEmbeddings
) -> IngestReport:
    """The real corpus chunked and embedded (fake embeddings) in the test DB."""
    async with session_maker() as session:
        report = await ingest(session, POLICIES_DIR, embedder)
        await session.commit()
    return report


@pytest.fixture
def make_user(session_maker: async_sessionmaker[AsyncSession]) -> MakeUser:
    """Insert a user holding `roles` (default: one) and return its auth headers."""

    async def _make(
        role: RoleName = RoleName.CUSTOMER,
        *,
        extra_roles: tuple[RoleName, ...] = (),
        email: str | None = None,
    ) -> Actor:
        address = email or f"{uuid.uuid4().hex}@test.example"
        async with session_maker() as session:
            catalog = await ensure_rbac(session)
            user = User(
                email=address,
                name="Test User",
                password_hash=PASSWORD_HASH,
                roles=[catalog[r] for r in (role, *extra_roles)],
            )
            session.add(user)
            await session.commit()
            user_id = user.id
        token = create_access_token(user_id=user_id)
        return Actor(user_id, {"Authorization": f"Bearer {token}"}, address)

    return _make


@pytest.fixture
async def client(
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """HTTP client whose requests use `session_maker`."""

    async def override() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http
    app.dependency_overrides.clear()


@dataclass
class PurchaseRef:
    """A purchase stored by `make_purchase`."""

    id: uuid.UUID
    customer_id: uuid.UUID
    course_id: uuid.UUID
    purchased_at: datetime
    amount: Decimal


MakeCourse = Callable[..., Awaitable[uuid.UUID]]
MakePurchase = Callable[..., Awaitable[PurchaseRef]]
MakeRefund = Callable[..., Awaitable[uuid.UUID]]


@pytest.fixture
def make_course(session_maker: async_sessionmaker[AsyncSession]) -> MakeCourse:
    """Insert a course and return its id."""

    async def _make(
        title: str = "Python for APIs",
        *,
        instructor_name: str = "Grace Hopper",
        price: Decimal | str = "49.99",
        currency: str = "USD",
    ) -> uuid.UUID:
        async with session_maker() as session:
            course = Course(
                title=title,
                instructor_name=instructor_name,
                price=Decimal(price),
                currency=currency,
            )
            session.add(course)
            await session.commit()
            return course.id

    return _make


@pytest.fixture
def make_purchase(
    session_maker: async_sessionmaker[AsyncSession], make_course: MakeCourse
) -> MakePurchase:
    """Insert a purchase (with its progress) of `customer`, `days_ago` days old.

    `customer` is an `Actor` or a user id. `purchased_at` overrides `days_ago`.
    Set `certificate=True` to issue the certificate; `progress=None` skips the
    progress row.
    """

    async def _make(
        customer: Actor | uuid.UUID,
        *,
        days_ago: float = 5,
        progress: int | None = 0,
        certificate: bool = False,
        amount: Decimal | str = "49.99",
        currency: str = "USD",
        payment_method: PaymentMethod = PaymentMethod.CARD,
        purchase_type: PurchaseType = PurchaseType.INDIVIDUAL,
        status: PurchaseStatus = PurchaseStatus.ACTIVE,
        course_id: uuid.UUID | None = None,
        purchased_at: datetime | None = None,
    ) -> PurchaseRef:
        customer_id = customer.id if isinstance(customer, Actor) else customer
        when = purchased_at or datetime.now(UTC) - timedelta(days=days_ago)
        course = course_id or await make_course(price=amount, currency=currency)
        async with session_maker() as session:
            purchase = Purchase(
                customer_id=customer_id,
                course_id=course,
                amount=Decimal(amount),
                currency=currency,
                payment_method=payment_method,
                purchase_type=purchase_type,
                purchased_at=when,
                status=status,
            )
            session.add(purchase)
            await session.flush()
            if progress is not None:
                session.add(
                    CourseProgress(
                        purchase_id=purchase.id,
                        progress_pct=progress,
                        lectures_completed=progress // 2,
                        last_accessed_at=when if progress else None,
                        certificate_issued_at=when if certificate else None,
                    )
                )
            await session.commit()
            return PurchaseRef(purchase.id, customer_id, course, when, Decimal(amount))

    return _make


@pytest.fixture
def make_refund(session_maker: async_sessionmaker[AsyncSession]) -> MakeRefund:
    """Insert a refund request for `purchase` (a `PurchaseRef`); returns its id."""

    async def _make(
        purchase: PurchaseRef,
        *,
        status: RefundStatus = RefundStatus.PENDING_APPROVAL,
        reason_text: str = "The course content does not match its description.",
        method: RefundMethod = RefundMethod.ORIGINAL_PAYMENT,
        idempotency_key: str | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        # A complete snapshot by default: the customer schema must be able to read it.
        stored = (
            snapshot
            if snapshot is not None
            else {
                "eligible": True,
                "reason_codes": [],
                "warnings": [],
                "days_since_purchase": 5,
                "refund_window_days": 30,
                "progress_pct": 0,
                "policy_version": "test",
                "checked_at": datetime.now(UTC).isoformat(),
            }
        )
        async with session_maker() as session:
            refund = RefundRequest(
                customer_id=purchase.customer_id,
                purchase_id=purchase.id,
                status=status,
                reason_category=ReasonCategory.NOT_AS_DESCRIBED,
                reason_text=reason_text,
                refund_method=method,
                amount=purchase.amount,
                currency="USD",
                channel=Channel.MANUAL_FORM,
                eligibility_snapshot=stored,
                policy_version="test",
                idempotency_key=idempotency_key or uuid.uuid4().hex,
            )
            session.add(refund)
            await session.commit()
            return refund.id

    return _make


REDIS_TEST_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client = Redis.from_url(
        REDIS_TEST_URL, decode_responses=True, socket_connect_timeout=1
    )
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip("Redis is not reachable")
    await client.flushdb()
    previous = getattr(app.state, "redis", None)
    app.state.redis = client
    yield client
    app.state.redis = previous
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def broken_redis() -> AsyncIterator[Redis]:
    """A configured Redis that nobody listens to (outage)."""
    client = Redis.from_url(
        "redis://127.0.0.1:1/0", decode_responses=True, socket_connect_timeout=0.2
    )
    previous = getattr(app.state, "redis", None)
    app.state.redis = client
    yield client
    app.state.redis = previous
    await client.aclose()

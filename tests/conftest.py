import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient
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
from app.main import app  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.rbac import ensure_rbac  # noqa: E402

API = "/api/v1"
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
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except OperationalError as exc:  # pragma: no cover
        await engine.dispose()
        pytest.skip(f"Postgres unavailable: {exc}")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


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

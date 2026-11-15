import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli import seed as seed_module
from app.cli.seed import (
    USERS,
    UserSpec,
    render,
    reset,
    seed,
    sid,
    user_id,
)
from app.core.config import Settings
from app.core.permissions import RoleName
from app.core.security import decode_access_token
from app.models.course import Course
from app.models.purchase import CourseProgress, Purchase
from app.models.refund import RefundEvent, RefundRequest
from app.models.user import User

TTL = timedelta(hours=1)
Maker = async_sessionmaker[AsyncSession]
TABLES = (User, Course, Purchase, CourseProgress, RefundRequest, RefundEvent)
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


async def run_seed(maker: Maker, now: datetime | None = None) -> Any:
    async with maker() as session:
        report = await seed(session, ttl=TTL, now=now)
        await session.commit()
    return report


async def counts(maker: Maker) -> dict[str, int]:
    async with maker() as session:
        return {
            model.__tablename__: int(
                await session.scalar(select(func.count()).select_from(model)) or 0
            )
            for model in TABLES
        }


async def test_seed_creates_the_users(session_maker: Maker) -> None:
    report = await run_seed(session_maker)
    assert len(report.users) == len(USERS)
    assert (await counts(session_maker))["users"] == len(USERS)


async def test_seed_is_idempotent(session_maker: Maker) -> None:
    first = await run_seed(session_maker)
    after_first = await counts(session_maker)
    second = await run_seed(session_maker)

    assert await counts(session_maker) == after_first
    assert first.users == second.users


async def test_reset_removes_only_seed_users_and_seed_recreates(
    session_maker: Maker,
) -> None:
    await run_seed(session_maker)
    async with session_maker() as session:
        session.add(User(name="Eve", email="e@x.test", password_hash="!"))
        await session.commit()

    async with session_maker() as session:
        await reset(session)
        await session.commit()
    after_reset = await counts(session_maker)
    assert after_reset == {
        "users": 1,  # Eve is not a seed user, so she survives
        "courses": 0,
        "purchases": 0,
        "course_progress": 0,
        "refund_requests": 0,
        "refund_events": 0,
    }

    await run_seed(session_maker)
    assert (await counts(session_maker))["users"] == len(USERS) + 1


async def test_reset_keeps_refund_events_protected(session_maker: Maker) -> None:
    """`reset` lifts the append-only trigger only inside its own transaction."""
    await run_seed(session_maker)
    async with session_maker() as session:
        await reset(session)
        await session.commit()
    await run_seed(session_maker)

    async with session_maker() as session:
        with pytest.raises(IntegrityError, match="append-only"):
            await session.execute(update(RefundEvent).values(type="TAMPERED"))


async def test_tokens_identify_existing_users_with_their_role(
    session_maker: Maker,
) -> None:
    report = await run_seed(session_maker)
    by_label = {t.label: t for t in report.tokens}

    assert by_label["supervisor-1"].role is RoleName.SUPERVISOR
    assert decode_access_token(by_label["supervisor-1"].token) == user_id(
        "supervisor-1"
    )
    assert by_label["ann"].role is RoleName.CUSTOMER
    assert decode_access_token(by_label["ann"].token) == user_id("ann")
    assert len(report.tokens) == len(USERS)

    async with session_maker() as session:
        sup = await session.get(User, user_id("supervisor-1"), populate_existing=True)
        assert sup is not None


async def test_seed_roles_match_the_catalog(session_maker: Maker) -> None:
    from app.core.permissions import ROLE_PERMISSIONS
    from app.repositories.user import UserRepository

    await run_seed(session_maker)
    async with session_maker() as session:
        repo = UserRepository(session)
        sup = await repo.get_principal(user_id("supervisor-1"))
        ann = await repo.get_principal(user_id("ann"))
    assert sup is not None and ann is not None
    assert sup.roles == {"supervisor"} and ann.roles == {"customer"}
    assert sup.permissions == {str(p) for p in ROLE_PERMISSIONS[RoleName.SUPERVISOR]}
    assert ann.permissions == {str(p) for p in ROLE_PERMISSIONS[RoleName.CUSTOMER]}


async def test_render_hides_tokens_on_request(session_maker: Maker) -> None:
    report = await run_seed(session_maker)
    with_tokens = render(report, show_tokens=True, base_url="http://x")
    without = render(report, show_tokens=False, base_url="http://x")
    token = report.tokens[0].token
    assert token in with_tokens and "export SUPERVISOR_1=" in with_tokens
    assert token not in without and "<supervisor-1 token>" in without
    assert "/api/v1/auth/login" in with_tokens


async def test_cli_runs_twice_end_to_end(
    session_maker: Maker, capsys: pytest.CaptureFixture[str]
) -> None:
    # `main` owns its event loop, so run it in a worker thread.
    assert await asyncio.to_thread(seed_module.main, ["--no-tokens"]) == 0
    first = await counts(session_maker)
    assert await asyncio.to_thread(seed_module.main, ["--reset"]) == 0
    assert await counts(session_maker) == first
    out = capsys.readouterr().out
    assert "Seed data ready" in out


def test_refuses_to_run_in_production(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    prod = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost/db",
        jwt_secret="x" * 32,
        environment="production",
    )
    monkeypatch.setattr(seed_module, "get_settings", lambda: prod)
    assert seed_module.main([]) == 1
    assert "production" in capsys.readouterr().err


def test_rejects_non_positive_token_ttl(capsys: pytest.CaptureFixture[str]) -> None:
    assert seed_module.main(["--token-ttl-hours", "0"]) == 2
    assert "positive" in capsys.readouterr().err


def test_seed_ids_are_stable() -> None:
    assert sid("user:ann") == sid("user:ann")
    assert sid("user:ann") != sid("user:bob")
    assert UserSpec("x", "X", "x@x.test", RoleName.CUSTOMER).role is RoleName.CUSTOMER


async def test_seed_users_can_log_in_with_the_seed_password(
    session_maker: Maker,
) -> None:
    from app.cli.seed import SEED_PASSWORD
    from app.core.passwords import verify_password

    await run_seed(session_maker)
    async with session_maker() as session:
        users = (await session.scalars(select(User))).all()
    assert len(users) == len(USERS)
    for user in users:
        assert verify_password(SEED_PASSWORD, user.password_hash)[0] is True


async def test_reseed_keeps_hashes_and_repairs_pre_password_users(
    session_maker: Maker,
) -> None:
    from sqlalchemy import update

    from app.cli.seed import SEED_PASSWORD
    from app.core.passwords import UNUSABLE_HASH, verify_password

    await run_seed(session_maker)
    async with session_maker() as session:
        before = (await session.get(User, user_id("ann"))).password_hash  # type: ignore[union-attr]
        await session.execute(
            update(User)
            .where(User.id == user_id("bob"))
            .values(password_hash=UNUSABLE_HASH)
        )
        await session.commit()

    await run_seed(session_maker)
    async with session_maker() as session:
        ann = await session.get(User, user_id("ann"), populate_existing=True)
        bob = await session.get(User, user_id("bob"), populate_existing=True)
    assert ann is not None and bob is not None
    assert ann.password_hash == before  # untouched
    assert verify_password(SEED_PASSWORD, bob.password_hash)[0] is True

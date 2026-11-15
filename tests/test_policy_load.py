"""Policy loading: front-matter -> policy_documents -> PolicyConfig."""

import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli.seed import seed, user_id
from app.core.security import create_access_token
from app.domain.enums import PolicyStatus
from app.domain.policy_config import PolicyConfigError
from app.models.policy import PolicyDocument
from app.policy.load import load_policies
from app.policy.loader import load_policy_directory
from app.repositories.policy import PolicyRepository
from conftest import API, POLICIES_DIR

Maker = async_sessionmaker[AsyncSession]
ELIGIBILITY = "refund-eligibility.md"


@pytest.fixture
def policies(tmp_path: Path) -> Path:
    target = tmp_path / "policies"
    shutil.copytree(POLICIES_DIR, target)
    return target


def edit(directory: Path, name: str, old: str, new: str) -> None:
    path = directory / name
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


async def test_the_six_documents_are_loaded_and_active(session_maker: Maker) -> None:
    async with session_maker() as session:
        docs = (await session.scalars(select(PolicyDocument))).all()
        config = await PolicyRepository(session).get_active_config()
    assert len(docs) == 6
    assert {d.status for d in docs} == {PolicyStatus.ACTIVE}
    assert all(len(d.checksum) == 64 for d in docs)
    assert config.refund_window_days == 30
    assert config.max_progress_pct == 30
    assert config.confirmation_ttl_min == 10 and config.sla_hours == 48
    assert config.policy_version == "2026-10-01"
    assert "OUTSIDE_REFUND_WINDOW" in config.reason_code_map


async def test_loading_twice_is_idempotent(session_maker: Maker) -> None:
    async with session_maker() as session:
        report = await load_policies(session, POLICIES_DIR)
        await session.commit()
        total = len((await session.scalars(select(PolicyDocument))).all())
    assert report.activated == () and len(report.unchanged) == 6
    assert total == 6


async def test_a_new_version_supersedes_the_previous_one_atomically(
    session_maker: Maker, policies: Path
) -> None:
    edit(policies, ELIGIBILITY, 'version: "2026-10-01"', 'version: "2026-12-01"')
    edit(policies, ELIGIBILITY, "refund_window_days: 30", "refund_window_days: 14")
    async with session_maker() as session:
        report = await load_policies(session, policies)
        await session.commit()
        rows = (
            await session.scalars(
                select(PolicyDocument).where(
                    PolicyDocument.doc_key == "refund-eligibility"
                )
            )
        ).all()
        config = await PolicyRepository(session).get_active_config()
    assert report.activated == ("refund-eligibility",)
    assert {(r.version, r.status) for r in rows} == {
        ("2026-10-01", PolicyStatus.SUPERSEDED),
        ("2026-12-01", PolicyStatus.ACTIVE),
    }
    assert config.refund_window_days == 14
    assert config.policy_version == "2026-12-01"


async def test_changing_a_param_and_reloading_changes_the_result(
    client: AsyncClient, session_maker: Maker, policies: Path
) -> None:
    async with session_maker() as session:
        report = await seed(session, ttl=timedelta(hours=1))
        await session.commit()
    outside = next(c for c in report.cases if c.spec.key == "outside-window")
    headers = {"Authorization": f"Bearer {create_access_token(user_id=user_id('ann'))}"}
    path = f"{API}/purchases/{outside.purchase_id}/refund-eligibility"

    before = (await client.get(path, headers=headers)).json()
    assert before["reason_codes"] == ["OUTSIDE_REFUND_WINDOW"]  # day 31 > 30

    edit(policies, ELIGIBILITY, "refund_window_days: 30", "refund_window_days: 45")
    async with session_maker() as session:
        await load_policies(session, policies)
        await session.commit()

    after = (await client.get(path, headers=headers)).json()
    assert after["eligible"] is True
    assert after["refund_window_days"] == 45


async def test_a_missing_param_does_not_load(
    session_maker: Maker, policies: Path
) -> None:
    edit(policies, ELIGIBILITY, "  max_progress_pct: 30\n", "")
    edit(policies, ELIGIBILITY, 'version: "2026-10-01"', 'version: "2027-01-01"')
    async with session_maker() as session:
        with pytest.raises(PolicyConfigError, match="max_progress_pct"):
            await load_policies(session, policies)
        await session.rollback()
        versions = {
            d.version for d in (await session.scalars(select(PolicyDocument))).all()
        }
    assert versions == {"2026-10-01"}  # nothing was activated


async def test_a_mistyped_param_does_not_load(
    session_maker: Maker, policies: Path
) -> None:
    edit(policies, ELIGIBILITY, "refund_window_days: 30", "refund_window_days: soon")
    async with session_maker() as session:
        with pytest.raises(PolicyConfigError, match="refund_window_days"):
            await load_policies(session, policies)


async def test_startup_fails_without_an_active_policy(session_maker: Maker) -> None:
    from app.main import app, lifespan

    async with session_maker() as session:
        for doc in (await session.scalars(select(PolicyDocument))).all():
            doc.status = PolicyStatus.SUPERSEDED
        await session.commit()
        with pytest.raises(PolicyConfigError):
            await PolicyRepository(session).get_active_config()
    with pytest.raises(PolicyConfigError):
        async with lifespan(app):
            pass


def test_front_matter_errors_are_reported(tmp_path: Path) -> None:
    (tmp_path / "bad.md").write_text("# no front matter\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError, match="front-matter"):
        load_policy_directory(tmp_path)


def test_an_empty_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(PolicyConfigError, match="No policy documents"):
        load_policy_directory(tmp_path)


def test_the_repo_policies_parse() -> None:
    files = load_policy_directory(POLICIES_DIR)
    assert {f.meta.doc_key for f in files} == {
        "refund-eligibility",
        "refund-process",
        "refund-methods",
        "special-cases",
        "refund-status-and-timelines",
        "refund-faq",
    }

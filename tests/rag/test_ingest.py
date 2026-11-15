import shutil
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import PolicyStatus
from app.domain.policy_config import PolicyConfigError
from app.models.policy import PolicyDocument
from app.models.policy_chunk import PolicyChunk
from app.rag import ingest as ingest_module
from app.rag.embeddings import FakeEmbeddings
from app.rag.ingest import IngestReport, ingest
from conftest import POLICIES_DIR

Maker = async_sessionmaker[AsyncSession]


def bump_faq_version(corpus: Path) -> None:
    path = corpus / "refund-faq.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'version: "2026-10-01"', 'version: "2026-11-01"'
        ),
        encoding="utf-8",
    )


async def chunk_state(maker: Maker) -> list[tuple[str, int]]:
    """Every chunk id with its row version (`xmin` changes on any write)."""
    async with maker() as session:
        rows = await session.execute(
            text("select id::text, xmin::text::bigint from policy_chunks order by id")
        )
        return [(r[0], int(r[1])) for r in rows]


async def test_ingest_creates_chunks_and_embedding_metadata(
    session_maker: Maker, ingested: IngestReport
) -> None:
    assert len(ingested.ingested) == 6 and ingested.chunks > 20
    async with session_maker() as session:
        docs = (await session.scalars(select(PolicyDocument))).all()
        assert {d.embedding_model for d in docs} == {"fake"}
        assert {d.embedding_dim for d in docs} == {FakeEmbeddings().dim}
        total = await session.scalar(select(func.count(PolicyChunk.id)))
        assert total == ingested.chunks


async def test_second_run_is_a_noop(
    session_maker: Maker, embedder: FakeEmbeddings, ingested: IngestReport
) -> None:
    before = await chunk_state(session_maker)
    async with session_maker() as session:
        report = await ingest(session, POLICIES_DIR, embedder)
        await session.commit()
    assert report.ingested == () and len(report.unchanged) == 6
    assert await chunk_state(session_maker) == before


async def test_force_and_strategy_change_reingest(
    session_maker: Maker, embedder: FakeEmbeddings, ingested: IngestReport
) -> None:
    async with session_maker() as session:
        forced = await ingest(session, POLICIES_DIR, embedder, force=True)
        other = await ingest(session, POLICIES_DIR, embedder, strategy="fixed_500")
        await session.commit()
    assert len(forced.ingested) == 6 and len(other.ingested) == 6
    async with session_maker() as session:
        strategies = set(
            (
                await session.scalars(
                    select(PolicyChunk.chunk_metadata["strategy"].as_string())
                )
            ).all()
        )
    assert strategies == {"fixed_500"}


async def test_failure_midway_rolls_back_everything(
    session_maker: Maker,
    embedder: FakeEmbeddings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "policies"
    shutil.copytree(POLICIES_DIR, corpus)
    bump_faq_version(corpus)
    calls = 0
    real = ingest_module.replace_chunks

    async def flaky(*args: Any, **kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("boom")
        await real(*args, **kwargs)

    monkeypatch.setattr(ingest_module, "replace_chunks", flaky)
    async with session_maker() as session:
        with pytest.raises(RuntimeError):
            await ingest(session, corpus, embedder)
        await session.rollback()

    async with session_maker() as session:
        active = (
            await session.execute(
                select(PolicyDocument.doc_key, func.count())
                .where(PolicyDocument.status == PolicyStatus.ACTIVE)
                .group_by(PolicyDocument.doc_key)
            )
        ).all()
        assert len(active) == 6 and all(n == 1 for _, n in active)
        versions = set((await session.scalars(select(PolicyDocument.version))).all())
        assert versions == {"2026-10-01"}
        assert await session.scalar(select(func.count(PolicyChunk.id))) == 0


async def test_new_version_supersedes_the_old_one(
    session_maker: Maker, embedder: FakeEmbeddings, tmp_path: Path
) -> None:
    corpus = tmp_path / "policies"
    shutil.copytree(POLICIES_DIR, corpus)
    async with session_maker() as session:
        await ingest(session, corpus, embedder)
        await session.commit()
    bump_faq_version(corpus)
    async with session_maker() as session:
        report = await ingest(session, corpus, embedder)
        await session.commit()
    assert report.ingested == ("refund-faq",) and len(report.unchanged) == 5
    async with session_maker() as session:
        rows = (
            await session.scalars(
                select(PolicyDocument).where(PolicyDocument.doc_key == "refund-faq")
            )
        ).all()
        assert {r.version: r.status for r in rows} == {
            "2026-10-01": PolicyStatus.SUPERSEDED,
            "2026-11-01": PolicyStatus.ACTIVE,
        }


async def test_missing_param_still_fails(
    session_maker: Maker, embedder: FakeEmbeddings, tmp_path: Path
) -> None:
    corpus = tmp_path / "policies"
    shutil.copytree(POLICIES_DIR, corpus)
    path = corpus / "refund-eligibility.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("  refund_window_days: 30\n", ""),
        encoding="utf-8",
    )
    async with session_maker() as session:
        with pytest.raises(PolicyConfigError):
            await ingest(session, corpus, embedder)

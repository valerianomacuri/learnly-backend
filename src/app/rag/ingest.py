"""RAG ingestion: `python -m app.rag.ingest` (entry point `rag-ingest`).

Reuses the policy loader of spec 02. Idempotent: a document whose checksum matches
the ACTIVE version, that already has chunks built with the same strategy and
embedding model, is left untouched (no writes). Otherwise chunks and embeddings are
computed first (the only network step), then the new version is activated and the
chunks replaced in one transaction.
"""

import argparse
import asyncio
import logging
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.domain.enums import PolicyStatus
from app.domain.policy_config import PolicyConfigError
from app.models.policy import PolicyDocument
from app.models.policy_chunk import PolicyChunk
from app.policy.load import activate_document, default_policies_dir
from app.policy.loader import (
    PolicyDocumentFile,
    config_from_files,
    load_policy_directory,
)
from app.rag.chunking import ChunkDraft, ChunkSource, get_chunker
from app.rag.embeddings import Embedder, EmbeddingsUnavailableError, get_embedder

log = logging.getLogger(__name__)

EMBED_BATCH = 64


@dataclass(frozen=True, slots=True)
class IngestReport:
    unchanged: tuple[str, ...] = ()
    ingested: tuple[str, ...] = ()
    chunks: int = 0


@dataclass(slots=True)
class _Pending:
    file: PolicyDocumentFile
    drafts: list[ChunkDraft]
    vectors: list[list[float]]
    activate: bool  # False: the version is already ACTIVE, only chunks are rebuilt


async def replace_chunks(
    session: AsyncSession,
    document: PolicyDocument,
    pending: _Pending,
    embedder: Embedder,
    strategy: str,
) -> None:
    """Swap the chunks of `document` for the new ones (caller owns the transaction)."""
    await session.execute(
        delete(PolicyChunk).where(PolicyChunk.document_id == document.id)
    )
    ids = [uuid.uuid4() for _ in pending.drafts]
    for index, (draft, vector) in enumerate(
        zip(pending.drafts, pending.vectors, strict=True)
    ):
        parent = None if draft.parent_index is None else ids[draft.parent_index]
        session.add(
            PolicyChunk(
                id=ids[index],
                document_id=document.id,
                parent_section_id=parent,
                section_path=draft.section_path,
                heading_level=draft.heading_level,
                chunk_type=draft.chunk_type,
                content=draft.content,
                embedded_text=draft.embedded_text,
                embedding=vector,
                tags=list(draft.tags),
                token_count=draft.token_count,
                source_lines=draft.source_lines,
                chunk_metadata={**draft.metadata, "strategy": strategy},
            )
        )
    document.embedding_model = embedder.model
    document.embedding_dim = embedder.dim
    await session.flush()


async def _is_current(
    session: AsyncSession, row: PolicyDocument, embedder: Embedder, strategy: str
) -> bool:
    if row.embedding_model != embedder.model or row.embedding_dim != embedder.dim:
        return False
    total, strategies = (
        await session.execute(
            select(
                func.count(PolicyChunk.id),
                func.count(func.distinct(PolicyChunk.chunk_metadata["strategy"])),
            ).where(PolicyChunk.document_id == row.id)
        )
    ).one()
    if total == 0 or strategies != 1:
        return False
    found = await session.scalar(
        select(PolicyChunk.chunk_metadata["strategy"].as_string())
        .where(PolicyChunk.document_id == row.id)
        .limit(1)
    )
    return found == strategy


def _embed(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        vectors.extend(embedder.embed_documents(texts[start : start + EMBED_BATCH]))
    return vectors


async def ingest(
    session: AsyncSession,
    directory: Path,
    embedder: Embedder,
    *,
    strategy: str = "structural",
    force: bool = False,
) -> IngestReport:
    """Validate, embed what changed, then write everything (caller commits)."""
    chunker = get_chunker(strategy)
    files = load_policy_directory(directory)
    config_from_files(files)  # fails before anything else if a param is missing

    active = {
        d.doc_key: d
        for d in (
            await session.scalars(
                select(PolicyDocument).where(
                    PolicyDocument.status == PolicyStatus.ACTIVE
                )
            )
        ).all()
    }
    unchanged: list[str] = []
    todo: list[_Pending] = []
    for file in files:
        row = active.get(file.meta.doc_key)
        same = row is not None and row.checksum == file.checksum
        if same and not force and await _is_current(session, row, embedder, strategy):  # type: ignore[arg-type]
            unchanged.append(file.meta.doc_key)
            continue
        drafts = chunker.split(
            ChunkSource(
                file.meta.doc_key, file.meta.title, file.content, tuple(file.meta.tags)
            )
        )
        vectors = _embed(embedder, [d.embedded_text for d in drafts])
        todo.append(_Pending(file, drafts, vectors, activate=not same))

    total = 0
    for pending in todo:
        key = pending.file.meta.doc_key
        if pending.activate:
            await activate_document(session, pending.file)
        row = await session.scalar(
            select(PolicyDocument).where(
                PolicyDocument.doc_key == key,
                PolicyDocument.status == PolicyStatus.ACTIVE,
            )
        )
        assert row is not None
        await replace_chunks(session, row, pending, embedder, strategy)
        total += len(pending.drafts)
    return IngestReport(
        tuple(unchanged), tuple(p.file.meta.doc_key for p in todo), total
    )


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rag-ingest", description="Chunk, embed and index the policies."
    )
    parser.add_argument("--dir", type=Path, default=None)
    parser.add_argument(
        "--strategy", default="structural", choices=["structural", "fixed_500"]
    )
    parser.add_argument("--force", action="store_true", help="rebuild even if current")
    return parser.parse_args(argv)


async def _run(
    directory: Path, embedder: Embedder, strategy: str, force: bool
) -> IngestReport:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            try:
                report = await ingest(
                    session, directory, embedder, strategy=strategy, force=force
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        return report
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    directory = args.dir or default_policies_dir()
    try:
        embedder = get_embedder()
    except EmbeddingsUnavailableError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        report = asyncio.run(
            _run(directory, embedder, args.strategy, args.force),
            loop_factory=asyncio.SelectorEventLoop,
        )
    except PolicyConfigError as exc:
        print(f"error: invalid policy: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: ingestion failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"rag-ingest: {len(report.ingested)} ingested ({report.chunks} chunks), "
        f"{len(report.unchanged)} unchanged"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

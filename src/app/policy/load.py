"""Load `policies/*.md` into `policy_documents`: `python -m app.policy.load`.

Idempotent by checksum. Activation is atomic: in one transaction the new version
becomes ACTIVE and the previous one SUPERSEDED. This command persists documents
and parameters only; chunking and embeddings are added by the RAG ingestion (07).
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.domain.enums import PolicyStatus
from app.domain.policy_config import PolicyConfigError
from app.models.policy import PolicyDocument
from app.policy.loader import (
    PolicyDocumentFile,
    config_from_files,
    load_policy_directory,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LoadReport:
    unchanged: tuple[str, ...] = ()
    activated: tuple[str, ...] = ()


def default_policies_dir() -> Path:
    configured = get_settings().policies_dir
    return configured or Path(__file__).resolve().parents[3] / "policies"


async def _activate(session: AsyncSession, file: PolicyDocumentFile) -> None:
    meta = file.meta
    rows = (
        await session.scalars(
            select(PolicyDocument).where(PolicyDocument.doc_key == meta.doc_key)
        )
    ).all()
    for row in rows:
        if row.status is PolicyStatus.ACTIVE:
            row.status = PolicyStatus.SUPERSEDED
    await session.flush()  # one ACTIVE row per doc_key (partial unique index)

    target = next((r for r in rows if r.version == meta.version), None)
    if target is None:
        session.add(
            PolicyDocument(
                doc_key=meta.doc_key,
                version=meta.version,
                effective_date=meta.effective_date,
                checksum=file.checksum,
                status=PolicyStatus.ACTIVE,
                params=file.stored_params,
            )
        )
    else:
        # Same version, other content: (doc_key, version) is unique, so the row is
        # updated in place. Bumping `version` keeps the history; this does not.
        if target.checksum != file.checksum:
            log.warning(
                "%s: content changed without a version bump (%s)",
                meta.doc_key,
                meta.version,
            )
        target.effective_date = meta.effective_date
        target.checksum = file.checksum
        target.params = file.stored_params
        target.status = PolicyStatus.ACTIVE
    await session.flush()


async def load_policies(session: AsyncSession, directory: Path) -> LoadReport:
    """Validate the whole set first, then activate what changed (caller commits)."""
    files = load_policy_directory(directory)
    config_from_files(files)  # raises PolicyConfigError before touching the database

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
    activated: list[str] = []
    for file in files:
        current = active.get(file.meta.doc_key)
        if current is not None and current.checksum == file.checksum:
            unchanged.append(file.meta.doc_key)
            continue
        await _activate(session, file)
        activated.append(file.meta.doc_key)
    return LoadReport(tuple(unchanged), tuple(activated))


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="policies-load", description="Load the refund policies (idempotent)."
    )
    parser.add_argument(
        "--dir", type=Path, default=None, help="directory with the policy .md files"
    )
    return parser.parse_args(argv)


async def _run(directory: Path) -> LoadReport:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            try:
                report = await load_policies(session, directory)
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
        # psycopg async can't use Windows' default ProactorEventLoop.
        report = asyncio.run(_run(directory), loop_factory=asyncio.SelectorEventLoop)
    except PolicyConfigError as exc:
        print(f"error: invalid policy: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: loading policies failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"policies: {len(report.activated)} activated, "
        f"{len(report.unchanged)} unchanged"
    )
    for key in report.activated:
        print(f"  + {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

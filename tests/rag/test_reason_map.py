import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import ReasonCode
from app.domain.policy_config import ELIGIBILITY_DOC_KEY
from app.models.policy import PolicyDocument
from app.models.policy_chunk import PolicyChunk
from app.policy.loader import config_from_files, load_policy_directory
from app.rag.chunking import ChunkSource, StructuralChunker
from app.rag.ingest import IngestReport
from app.rag.reason_map import (
    METHOD_CITATIONS,
    citations_for,
    eligible_citations,
    unqualify,
)
from conftest import POLICIES_DIR

FILES = load_policy_directory(POLICIES_DIR)
CONFIG = config_from_files(FILES)


def corpus_paths() -> set[tuple[str, str]]:
    return {
        (f.meta.doc_key, c.section_path)
        for f in FILES
        for c in StructuralChunker().split(
            ChunkSource(f.meta.doc_key, f.meta.title, f.content)
        )
    }


MAPPED = [c for c in ReasonCode if c.value in CONFIG.reason_code_map]


def test_every_reason_code_except_not_found_is_mapped() -> None:
    unmapped = {c for c in ReasonCode if c not in MAPPED}
    assert unmapped == {ReasonCode.PURCHASE_NOT_FOUND}


@pytest.mark.parametrize("code", MAPPED, ids=lambda c: c.value)
def test_reason_code_resolves_to_an_existing_section(code: ReasonCode) -> None:
    (citation,) = citations_for(CONFIG, [code.value])
    assert citation.doc_key == ELIGIBILITY_DOC_KEY
    assert (citation.doc_key, unqualify(citation.section_path)) in corpus_paths()


def test_method_citations_exist_in_the_corpus() -> None:
    paths = corpus_paths()
    for citation in METHOD_CITATIONS.values():
        assert (citation.doc_key, unqualify(citation.section_path)) in paths


def test_citations_are_deduplicated_and_unknown_codes_ignored() -> None:
    result = citations_for(
        CONFIG, ["ALREADY_REFUNDED", "ACTIVE_REFUND_EXISTS", "PURCHASE_NOT_FOUND"]
    )
    assert len(result) == 1
    assert eligible_citations(CONFIG, None)[0].section_path.endswith(
        "Ventana de 30 días"
    )


async def test_stored_chunks_contain_every_cited_section(
    session_maker: async_sessionmaker[AsyncSession], ingested: IngestReport
) -> None:
    async with session_maker() as session:
        stored = {
            (key, path)
            for key, path in (
                await session.execute(
                    select(PolicyDocument.doc_key, PolicyChunk.section_path).join(
                        PolicyChunk, PolicyChunk.document_id == PolicyDocument.id
                    )
                )
            ).all()
        }
    for code in MAPPED:
        (citation,) = citations_for(CONFIG, [code.value])
        assert (citation.doc_key, unqualify(citation.section_path)) in stored

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.rag.embeddings import FakeEmbeddings
from app.rag.ingest import IngestReport
from app.rag.llm import FakeChatModel
from app.rag.retriever import PolicyRetriever, detect_language, rrf_fuse

Maker = async_sessionmaker[AsyncSession]


def test_rrf_rewards_agreement_between_rankings() -> None:
    a, b, c = (uuid.uuid4() for _ in range(3))
    fused = rrf_fuse([[a, b], [b, c]], k=60)
    assert fused[b] > fused[a] > fused[c]
    assert fused[b] == 1 / 62 + 1 / 61
    assert rrf_fuse([]) == {}


def test_detect_language() -> None:
    assert detect_language("¿Cuántos días tengo para pedir un reembolso?") == "es"
    assert detect_language("How many days do I have to ask for a refund?") == "en"


async def test_relevant_question_finds_the_window_section(
    session_maker: Maker, embedder: FakeEmbeddings, ingested: IngestReport
) -> None:
    async with session_maker() as session:
        result = await PolicyRetriever(session, embedder, threshold=0.2).retrieve(
            "¿Cuántos días tengo para pedir un reembolso?"
        )
    assert not result.insufficient_evidence
    assert len(result.chunks) <= 5
    paths = [c.section_path for c in result.chunks]
    assert any("Ventana de 30 días" in p or "Cuánto tiempo" in p for p in paths)
    assert result.best_score >= 0.2
    assert any(line.startswith("rrf") for line in result.trace)
    assert "rerank: unavailable, using RRF order" in result.trace


async def test_relevance_gate_rejects_out_of_scope_question(
    session_maker: Maker, embedder: FakeEmbeddings, ingested: IngestReport
) -> None:
    async with session_maker() as session:
        result = await PolicyRetriever(session, embedder, threshold=0.3).retrieve(
            "quark gluon boson hadron"
        )
    assert result.insufficient_evidence
    assert result.best_score < 0.3


async def test_tag_filter_and_only_active_documents(
    session_maker: Maker, embedder: FakeEmbeddings, ingested: IngestReport
) -> None:
    async with session_maker() as session:
        retriever = PolicyRetriever(session, embedder, threshold=0.0)
        result = await retriever.retrieve("reembolso", tags=["credits"])
        assert result.chunks
        assert {c.doc_key for c in result.chunks} <= {"refund-methods", "refund-faq"}


async def test_parent_expansion_and_dedupe(
    session_maker: Maker, embedder: FakeEmbeddings, ingested: IngestReport
) -> None:
    async with session_maker() as session:
        result = await PolicyRetriever(session, embedder, threshold=0.0).retrieve(
            "suscripciones no son elegibles"
        )
    assert len({c.id for c in result.chunks}) == len(result.chunks)
    for chunk in result.chunks:
        if chunk.parent_id is not None:
            assert chunk.parent_content


async def test_history_rewrites_the_query_only_when_present(
    session_maker: Maker, embedder: FakeEmbeddings, ingested: IngestReport
) -> None:
    llm = FakeChatModel(complete=lambda s, u: "plazo para pedir reembolso")
    async with session_maker() as session:
        retriever = PolicyRetriever(session, embedder, llm=llm, threshold=0.0)
        plain = await retriever.retrieve("y cuánto tiempo?")
        assert llm.calls == [] and plain.query == "y cuánto tiempo?"
        turns: Sequence[str] = ("user: quiero un reembolso",)
        rewritten = await retriever.retrieve("y cuánto tiempo?", history=turns)
    assert len(llm.calls) == 1
    assert rewritten.query == "plazo para pedir reembolso"

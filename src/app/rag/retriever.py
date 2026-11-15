"""Hybrid retrieval over the ACTIVE policy chunks (spec 07).

Pipeline: query rewrite (only with history) -> dense top-20 + lexical top-20 ->
RRF fusion -> optional cross-encoder rerank (fallback: RRF order) -> top 5 ->
parent expansion + dedupe -> relevance gate.

Gate score: with a reranker, the sigmoid of the best cross-encoder logit; without
one, the best cosine similarity among the final chunks. Below
`RELEVANCE_THRESHOLD` the result is `insufficient_evidence`.
"""

import math
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.domain.enums import PolicyStatus
from app.models.policy import PolicyDocument
from app.models.policy_chunk import PolicyChunk
from app.rag.embeddings import Embedder
from app.rag.llm import ChatModel

TOP_K_EACH = 20
RRF_K = 60
FINAL_K = 5

_SPANISH_HINTS = frozenset(
    "el la los las un una de del que por para con mi puedo cuántos cuántas cómo "
    "qué es en y o si no se me tengo reembolso días".split()
)
_ENGLISH_HINTS = frozenset(
    "the a an of to is are can do does how what my i you for and or if not "
    "refund days".split()
)
_WORD = re.compile(r"\w+", re.UNICODE)

REWRITE_SYSTEM = (
    "Rewrite the user's last question as a standalone search query, resolving "
    "references with the conversation. Keep the language. Answer with the query only."
)


class Reranker(Protocol):
    def score(self, query: str, texts: Sequence[str]) -> list[float]: ...


class CrossEncoderReranker:
    """`sentence-transformers` cross-encoder (extra `rag-rerank`, lazy import)."""

    def __init__(self, model: str) -> None:
        from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]

        self._model = CrossEncoder(model)

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        scores = self._model.predict([(query, t) for t in texts])
        return [float(s) for s in scores]


def get_reranker() -> Reranker | None:
    """The configured reranker, or None if the optional extra is not installed."""
    try:
        return CrossEncoderReranker(get_settings().rerank_model)
    except ImportError:
        return None


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    id: uuid.UUID
    doc_key: str
    section_path: str
    chunk_type: str
    content: str
    similarity: float
    score: float
    doc_title: str = ""
    parent_id: uuid.UUID | None = None
    parent_content: str | None = None


@dataclass(slots=True)
class RetrievalResult:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    insufficient_evidence: bool = False
    best_score: float = 0.0
    language: str = "es"
    query: str = ""
    trace: list[str] = field(default_factory=list)


def detect_language(text: str) -> str:
    words = {w.lower() for w in _WORD.findall(text)}
    en, es = len(words & _ENGLISH_HINTS), len(words & _SPANISH_HINTS)
    if re.search(r"[áéíóúñ¿¡]", text.lower()):
        es += 2
    return "en" if en > es else "es"


def rrf_fuse(
    rankings: Sequence[Sequence[uuid.UUID]], k: int = RRF_K
) -> dict[uuid.UUID, float]:
    """Reciprocal Rank Fusion: sum of 1 / (k + rank) over the rankings."""
    fused: dict[uuid.UUID, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)
    return fused


def _or_query(text: str) -> str:
    words = [w for w in _WORD.findall(text.lower()) if len(w) > 2]
    return " or ".join(dict.fromkeys(words))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class PolicyRetriever:
    def __init__(
        self,
        session: AsyncSession,
        embedder: Embedder,
        *,
        llm: ChatModel | None = None,
        reranker: Reranker | None = None,
        threshold: float | None = None,
    ) -> None:
        self.session = session
        self.embedder = embedder
        self.llm = llm
        self.reranker = reranker
        self.threshold = (
            get_settings().relevance_threshold if threshold is None else threshold
        )

    def _rewrite(self, query: str, history: Sequence[str], trace: list[str]) -> str:
        if not history or self.llm is None:
            trace.append("rewrite: skipped")
            return query
        convo = "\n".join(history[-6:])
        rewritten = self.llm.complete(
            REWRITE_SYSTEM, f"Conversation:\n{convo}\n\nQuestion: {query}"
        ).strip()
        trace.append(f"rewrite: {rewritten!r}")
        return rewritten or query

    async def _candidates(
        self, query: str, tags: Sequence[str] | None
    ) -> tuple[list[uuid.UUID], list[uuid.UUID], dict[uuid.UUID, float]]:
        vector = self.embedder.embed_query(query)
        distance = PolicyChunk.embedding.cosine_distance(vector)
        active = PolicyDocument.status == PolicyStatus.ACTIVE
        conditions: list[Any] = [active]
        if tags:
            conditions.append(PolicyChunk.tags.overlap(list(tags)))

        dense_rows = (
            await self.session.execute(
                select(PolicyChunk.id, (1 - distance).label("sim"))
                .join(PolicyDocument, PolicyDocument.id == PolicyChunk.document_id)
                .where(*conditions)
                .order_by(distance)
                .limit(TOP_K_EACH)
            )
        ).all()
        sims = {row.id: float(row.sim) for row in dense_rows}

        lexical: list[uuid.UUID] = []
        text_query = _or_query(query)
        if text_query:
            tsq = func.websearch_to_tsquery("spanish", text_query)
            rank = func.ts_rank(PolicyChunk.tsv, tsq)
            lex_rows = (
                await self.session.execute(
                    select(PolicyChunk.id, (1 - distance).label("sim"))
                    .join(PolicyDocument, PolicyDocument.id == PolicyChunk.document_id)
                    .where(*conditions, PolicyChunk.tsv.op("@@")(tsq))
                    .order_by(rank.desc())
                    .limit(TOP_K_EACH)
                )
            ).all()
            lexical = [row.id for row in lex_rows]
            for row in lex_rows:
                sims.setdefault(row.id, float(row.sim))
        return [row.id for row in dense_rows], lexical, sims

    async def retrieve(
        self,
        query: str,
        *,
        history: Sequence[str] = (),
        tags: Sequence[str] | None = None,
    ) -> RetrievalResult:
        trace: list[str] = []
        language = detect_language(query)
        standalone = self._rewrite(query, history, trace)
        dense, lexical, sims = await self._candidates(standalone, tags)
        trace.append(f"dense: {len(dense)} lexical: {len(lexical)}")

        fused = rrf_fuse([dense, lexical])
        ordered = sorted(fused, key=lambda k: fused[k], reverse=True)
        trace.append(f"rrf: {len(ordered)} candidates")
        result = RetrievalResult(language=language, query=standalone, trace=trace)
        if not ordered:
            result.insufficient_evidence = True
            trace.append("gate: no candidates")
            return result

        rows = {
            row.id: row
            for row in (
                await self.session.scalars(
                    select(PolicyChunk).where(PolicyChunk.id.in_(ordered))
                )
            ).all()
        }
        docs = {
            row.id: (row.doc_key, row.title or row.doc_key)
            for row in (
                await self.session.execute(
                    select(
                        PolicyDocument.id, PolicyDocument.doc_key, PolicyDocument.title
                    ).where(
                        PolicyDocument.id.in_({r.document_id for r in rows.values()})
                    )
                )
            ).all()
        }

        scores = dict(fused)
        if self.reranker is not None:
            head = ordered[: TOP_K_EACH * 2]
            raw = self.reranker.score(standalone, [rows[i].content for i in head])
            scores = {i: _sigmoid(s) for i, s in zip(head, raw, strict=True)}
            ordered = sorted(head, key=lambda k: scores[k], reverse=True)
            trace.append("rerank: cross-encoder")
        else:
            trace.append("rerank: unavailable, using RRF order")
        top = ordered[:FINAL_K]

        parent_ids = {rows[i].parent_section_id for i in top} - {None}
        parents = {
            p.id: p
            for p in (
                await self.session.scalars(
                    select(PolicyChunk).where(PolicyChunk.id.in_(parent_ids))
                )
            ).all()
        }
        seen: set[uuid.UUID] = set()
        for chunk_id in top:
            row = rows[chunk_id]
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            parent_key = row.parent_section_id
            parent = parents.get(parent_key) if parent_key else None
            result.chunks.append(
                RetrievedChunk(
                    id=row.id,
                    doc_key=docs[row.document_id][0],
                    doc_title=docs[row.document_id][1],
                    section_path=row.section_path,
                    chunk_type=row.chunk_type,
                    content=row.content,
                    similarity=sims.get(chunk_id, 0.0),
                    score=scores[chunk_id],
                    parent_id=parent.id if parent else None,
                    parent_content=parent.content if parent else None,
                )
            )
        # Expansion may surface a parent that is also a hit: keep a single copy.
        hit_ids = {c.id for c in result.chunks}
        result.chunks = [
            c if c.parent_id not in hit_ids else _without_parent(c)
            for c in result.chunks
        ]

        gate = (
            max(c.score for c in result.chunks)
            if self.reranker is not None
            else max(c.similarity for c in result.chunks)
        )
        result.best_score = gate
        if gate < self.threshold:
            result.insufficient_evidence = True
            trace.append(f"gate: {gate:.3f} < {self.threshold:.3f} -> insufficient")
        else:
            trace.append(f"gate: {gate:.3f} >= {self.threshold:.3f}")
        return result


def _without_parent(chunk: RetrievedChunk) -> RetrievedChunk:
    return RetrievedChunk(
        id=chunk.id,
        doc_key=chunk.doc_key,
        doc_title=chunk.doc_title,
        section_path=chunk.section_path,
        chunk_type=chunk.chunk_type,
        content=chunk.content,
        similarity=chunk.similarity,
        score=chunk.score,
    )

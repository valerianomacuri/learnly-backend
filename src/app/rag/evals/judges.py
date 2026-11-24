"""Judge protocol of the RAG evals (spec 08) and the offline heuristic judge.

A judge scores one answered question and returns a `JudgeScores` (each value in
0..1, `None` when not measured). The real judge is `ragas_judge.RagasJudge`; the
lexical `HeuristicJudge` below is only the `--offline` proxy and is never gated.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.rag.retriever import RetrievedChunk
from app.schemas.refund import Citation


@dataclass(frozen=True, slots=True)
class JudgeInput:
    question: str
    answer: str
    citations: Sequence[Citation]
    chunks: Sequence[RetrievedChunk]
    # Ground truth (`expected_answer_facts` joined); None for `should_abstain` cases.
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class JudgeScores:
    faithfulness: float | None = None
    citation_precision: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None
    answer_relevancy: float | None = None


class Judge(Protocol):
    async def score(self, item: JudgeInput) -> JudgeScores: ...


def evidence_for(citation: Citation, chunks: Sequence[RetrievedChunk]) -> str | None:
    """Content of the retrieved chunk a citation points to (None: not retrieved)."""
    for chunk in chunks:
        if chunk.doc_key == citation.doc_key and citation.section_path.endswith(
            chunk.section_path
        ):
            return chunk.content
    return None


def context_text(chunks: Sequence[RetrievedChunk]) -> str:
    return "\n---\n".join(
        f"[{c.doc_key} | {c.section_path}]\n{c.content}" for c in chunks
    )


_WORD = re.compile(r"\w+", re.UNICODE)


def _stems(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return {w[:5] for w in _WORD.findall(folded) if len(w) > 3 or w.isdigit()}


class HeuristicJudge:
    """Lexical proxy: a sentence is supported when its word stems are in the context."""

    def __init__(self, support: float = 0.6) -> None:
        self.support = support

    def _supported(self, sentence: str, evidence: str) -> bool:
        words = _stems(sentence)
        return not words or len(words & _stems(evidence)) / len(words) >= self.support

    def _faithfulness(self, item: JudgeInput) -> float | None:
        sentences = [
            s for s in re.split(r"(?<=[.!?])\s+|\n+", item.answer) if s.strip()
        ]
        if not sentences:
            return None
        evidence = context_text(item.chunks)
        return sum(self._supported(s, evidence) for s in sentences) / len(sentences)

    def _citation_precision(self, item: JudgeInput) -> float | None:
        if not item.citations:
            return None
        good = 0
        for citation in item.citations:
            evidence = evidence_for(citation, item.chunks)
            if evidence is not None and self._supported(item.answer, evidence):
                good += 1
        return good / len(item.citations)

    async def score(self, item: JudgeInput) -> JudgeScores:
        return JudgeScores(
            faithfulness=self._faithfulness(item),
            citation_precision=self._citation_precision(item),
        )

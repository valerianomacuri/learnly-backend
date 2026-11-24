"""`RagasJudge` adapter with fake RAGAS metrics (no network, no key)."""

import uuid
from dataclasses import dataclass
from typing import Any

from app.rag.evals.judges import JudgeInput
from app.rag.evals.ragas_judge import RagasJudge, RagasMetrics
from app.rag.retriever import RetrievedChunk
from app.schemas.refund import Citation


@dataclass
class _Result:
    value: float


class _FakeMetric:
    def __init__(self, score: float | None = 1.0, *, fail: bool = False) -> None:
        self.score = score
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def ascore(self, **kwargs: Any) -> _Result:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("boom")
        return _Result(float("nan") if self.score is None else self.score)


def _chunk(section: str, content: str) -> RetrievedChunk:
    return RetrievedChunk(
        id=uuid.uuid4(),
        doc_key="refund-policy",
        section_path=section,
        chunk_type="section",
        content=content,
        similarity=1.0,
        score=1.0,
    )


def _judge(**overrides: _FakeMetric) -> tuple[RagasJudge, RagasMetrics]:
    metrics = RagasMetrics(
        faithfulness=overrides.get("faithfulness", _FakeMetric(0.5)),
        context_precision=overrides.get("context_precision", _FakeMetric(0.8)),
        context_recall=overrides.get("context_recall", _FakeMetric(0.6)),
        answer_relevancy=overrides.get("answer_relevancy", _FakeMetric(0.9)),
    )
    return RagasJudge(metrics), metrics


def _item(reference: str | None = "30 días") -> JudgeInput:
    return JudgeInput(
        question="¿Plazo?",
        answer="30 días.",
        citations=[Citation(doc_key="refund-policy", section_path="Ventana")],
        chunks=[_chunk("Ventana", "30 días"), _chunk("Otro", "otra cosa")],
        reference=reference,
    )


async def test_maps_the_sample_to_each_metric() -> None:
    judge, metrics = _judge()
    scores = await judge.score(_item())
    assert scores.faithfulness == 0.5
    assert scores.context_precision == 0.8
    assert scores.context_recall == 0.6
    assert scores.answer_relevancy == 0.9
    assert isinstance(metrics.context_recall, _FakeMetric)
    assert metrics.context_recall.calls == [
        {
            "user_input": "¿Plazo?",
            "reference": "30 días",
            "retrieved_contexts": ["30 días", "otra cosa"],
        }
    ]


async def test_citation_precision_scores_each_cited_chunk_alone() -> None:
    faith = _FakeMetric(1.0)
    judge, _ = _judge(faithfulness=faith)
    scores = await judge.score(_item())
    assert scores.citation_precision == 1.0
    # one call over the whole context + one over the cited chunk only
    assert {tuple(c["retrieved_contexts"]) for c in faith.calls} == {
        ("30 días", "otra cosa"),
        ("30 días",),
    }


async def test_a_citation_to_an_unretrieved_chunk_counts_zero() -> None:
    judge, _ = _judge()
    item = JudgeInput(
        question="q",
        answer="a",
        citations=[
            Citation(doc_key="refund-policy", section_path="Ventana"),
            Citation(doc_key="refund-policy", section_path="No recuperada"),
        ],
        chunks=[_chunk("Ventana", "30 días")],
    )
    scores = await judge.score(item)
    assert scores.citation_precision == 0.25  # (0.5 + 0) / 2


async def test_without_reference_the_reference_metrics_are_skipped() -> None:
    judge, metrics = _judge()
    scores = await judge.score(_item(reference=None))
    assert scores.faithfulness == 0.5
    assert scores.context_precision is None
    assert scores.context_recall is None
    assert scores.answer_relevancy is None
    assert isinstance(metrics.context_precision, _FakeMetric)
    assert metrics.context_precision.calls == []


async def test_a_failing_or_nan_metric_is_none_not_a_crash() -> None:
    judge, _ = _judge(
        faithfulness=_FakeMetric(fail=True), context_recall=_FakeMetric(None)
    )
    scores = await judge.score(_item())
    assert scores.faithfulness is None
    assert scores.citation_precision is None
    assert scores.context_recall is None
    assert scores.context_precision == 0.8

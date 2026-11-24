"""RAGAS judge of the RAG evals (spec 08): generation quality scored by RAGAS.

Uses the `ragas.metrics.collections` API (the legacy `ragas.metrics` classes are
deprecated). Each metric is an object with `async ascore(**kwargs)` returning an
object with `.value`, so tests inject fakes through `RagasMetrics`.
"""

import asyncio
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from app.core.config import Settings
from app.rag.evals.judges import JudgeInput, JudgeScores, evidence_for
from app.rag.retriever import RetrievedChunk

logger = logging.getLogger(__name__)

# Seconds before a single RAGAS metric call is given up (the score becomes None).
METRIC_TIMEOUT_S = 120.0
# Concurrent RAGAS metric calls (each is one or more OpenAI requests) and retries of
# a failed call; without a cap the full run hits OpenAI timeouts and loses scores.
METRIC_CONCURRENCY = 6
METRIC_ATTEMPTS = 2


class _Result(Protocol):
    value: float


class Metric(Protocol):
    async def ascore(self, **kwargs: Any) -> _Result: ...


@dataclass(frozen=True, slots=True)
class RagasMetrics:
    faithfulness: Metric
    context_precision: Metric
    context_recall: Metric
    answer_relevancy: Metric


def build_metrics(settings: Settings) -> RagasMetrics:
    """RAGAS metrics over the configured OpenAI chat and embedding models (T=0)."""
    from openai import AsyncOpenAI
    from ragas.embeddings import OpenAIEmbeddings
    from ragas.llms import llm_factory
    from ragas.metrics.collections import (
        AnswerRelevancy,
        ContextPrecisionWithReference,
        ContextRecall,
        Faithfulness,
    )

    if settings.openai_api_key is None:
        raise RuntimeError("OPENAI_API_KEY is required for the RAGAS judge")
    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=60.0,
        max_retries=3,
    )
    llm = llm_factory(settings.llm_model, client=client, temperature=0)
    embeddings = OpenAIEmbeddings(client=client, model=settings.embedding_model)
    # RAGAS metrics take typed keyword arguments; `Metric` is the common shape.
    return RagasMetrics(
        faithfulness=cast(Metric, Faithfulness(llm=llm)),
        context_precision=cast(Metric, ContextPrecisionWithReference(llm=llm)),
        context_recall=cast(Metric, ContextRecall(llm=llm)),
        answer_relevancy=cast(Metric, AnswerRelevancy(llm=llm, embeddings=embeddings)),
    )


def _contexts(chunks: Sequence[RetrievedChunk]) -> list[str]:
    return [c.content for c in chunks]


class RagasJudge:
    """Scores answers with RAGAS. A failing metric yields None, not a crash."""

    def __init__(self, metrics: RagasMetrics) -> None:
        self.metrics = metrics
        self._gate = asyncio.Semaphore(METRIC_CONCURRENCY)

    @classmethod
    def from_settings(cls, settings: Settings) -> "RagasJudge":
        return cls(build_metrics(settings))

    async def _run(self, name: str, metric: Metric, **kwargs: Any) -> float | None:
        for attempt in range(1, METRIC_ATTEMPTS + 1):
            try:
                async with self._gate:
                    result = await asyncio.wait_for(
                        metric.ascore(**kwargs), timeout=METRIC_TIMEOUT_S
                    )
                break
            except Exception as exc:
                logger.warning(
                    "ragas metric %s failed (attempt %d): %r", name, attempt, exc
                )
                if attempt == METRIC_ATTEMPTS:
                    return None
        value = float(result.value)
        return None if math.isnan(value) else value

    async def _citation_precision(self, item: JudgeInput) -> float | None:
        """Mean Faithfulness of the answer against each cited chunk alone.

        RAGAS has no citation metric: a citation is good when the chunk it points to
        supports the answer by itself; one that was not retrieved counts 0.
        """
        if not item.citations:
            return None
        pairs = [(c, evidence_for(c, item.chunks)) for c in item.citations]
        scores = await asyncio.gather(
            *(
                self._run(
                    "citation",
                    self.metrics.faithfulness,
                    user_input=item.question,
                    response=item.answer,
                    retrieved_contexts=[evidence],
                )
                if evidence is not None
                else _zero()
                for _, evidence in pairs
            )
        )
        measured = [s for s in scores if s is not None]
        return sum(measured) / len(measured) if measured else None

    async def score(self, item: JudgeInput) -> JudgeScores:
        contexts = _contexts(item.chunks)
        tasks: dict[str, Any] = {
            "faithfulness": self._run(
                "faithfulness",
                self.metrics.faithfulness,
                user_input=item.question,
                response=item.answer,
                retrieved_contexts=contexts,
            ),
            "citation_precision": self._citation_precision(item),
        }
        if item.reference:
            tasks["context_precision"] = self._run(
                "context_precision",
                self.metrics.context_precision,
                user_input=item.question,
                reference=item.reference,
                retrieved_contexts=contexts,
            )
            tasks["context_recall"] = self._run(
                "context_recall",
                self.metrics.context_recall,
                user_input=item.question,
                reference=item.reference,
                retrieved_contexts=contexts,
            )
            tasks["answer_relevancy"] = self._run(
                "answer_relevancy",
                self.metrics.answer_relevancy,
                user_input=item.question,
                response=item.answer,
            )
        values = await asyncio.gather(*tasks.values())
        return JudgeScores(**dict(zip(tasks, values, strict=True)))


async def _zero() -> float:
    return 0.0

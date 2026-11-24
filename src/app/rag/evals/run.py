"""`python -m app.rag.evals.run [--strategy structural|fixed_500] [--offline]`.

Exit code 1 when a metric is below its threshold (`evals/thresholds.toml`).
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from app.core.config import get_settings
from app.rag.embeddings import EmbeddingsUnavailableError, FakeEmbeddings, get_embedder
from app.rag.evals import report
from app.rag.evals.fakes import offline_chat
from app.rag.evals.golden import load_golden
from app.rag.evals.judges import HeuristicJudge, Judge
from app.rag.evals.ragas_judge import RagasJudge
from app.rag.evals.runner import RunResult, evaluate
from app.rag.llm import get_chat_model
from app.rag.retriever import get_reranker


async def run(strategy: str, offline: bool) -> RunResult:
    cases = load_golden()
    if offline:
        return await evaluate(
            cases,
            strategy=strategy,
            mode="offline",
            embedder=FakeEmbeddings(),
            llm=offline_chat(),
            judge=HeuristicJudge(),
            reranker=None,
        )
    embedder, llm = get_embedder(), get_chat_model()
    judge: Judge = RagasJudge.from_settings(get_settings())
    return await evaluate(
        cases,
        strategy=strategy,
        mode="full",
        embedder=embedder,
        llm=llm,
        judge=judge,
        reranker=get_reranker(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rag.evals.run", description="RAG evals.")
    parser.add_argument(
        "--strategy", default="structural", choices=["structural", "fixed_500"]
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="fake embeddings + extractive LLM + heuristic judges (no key, no network)",
    )
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(
            run(args.strategy, args.offline), loop_factory=asyncio.SelectorEventLoop
        )
    except EmbeddingsUnavailableError as exc:
        print(f"error: {exc} (use --offline to run without a key)", file=sys.stderr)
        return 2

    rows = report.evaluate_thresholds(
        result.summary(), report.load_thresholds(result.mode)
    )
    print(report.console_table(result, rows))
    json_path, md_path = report.write_reports(result, rows)
    print(f"\nreports: {json_path}\n         {md_path}")
    bad = report.failed(rows)
    if bad:
        print(
            "\nFAILED: " + ", ".join(report.LABELS[r.metric] for r in bad),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

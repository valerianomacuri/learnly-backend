"""`python -m app.agent.evals.run [--fake]` (spec 13).

`--fake` replays the scripts with a scripted LLM and fake embeddings (CI: no key,
no network). Without it the real model and embeddings are used (needs
`OPENAI_API_KEY`). Exit code 1 when a metric misses its threshold
(`evals/thresholds.toml`, section `[agent]`).
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from app.agent.evals import report
from app.agent.evals.fakes import fake_llm
from app.agent.evals.runner import RunResult, evaluate, load_scripts
from app.agent.llm import get_chat_model
from app.rag.embeddings import EmbeddingsUnavailableError, FakeEmbeddings, get_embedder


async def run(fake: bool) -> RunResult:
    scripts = load_scripts()
    if fake:
        return await evaluate(
            scripts, mode="fake", embedder=FakeEmbeddings(), llm_factory=fake_llm
        )
    # Real mode: the model extracts purchase/reason itself; only the customer's
    # decisions at the confirmation card stay scripted.
    return await evaluate(
        scripts,
        mode="real",
        embedder=get_embedder(),
        llm_factory=lambda _facts: get_chat_model(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent.evals.run", description="Agent evals.")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="scripted LLM + fake embeddings (no key, no network)",
    )
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run(args.fake), loop_factory=asyncio.SelectorEventLoop)
    except EmbeddingsUnavailableError as exc:
        print(f"error: {exc} (use --fake to run without a key)", file=sys.stderr)
        return 2

    rows = report.evaluate_thresholds(result.summary(), report.load_thresholds())
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
    sys.exit(main())

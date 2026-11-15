"""Full evals with the real OpenAI API: `pytest -m live` (needs OPENAI_API_KEY)."""

import os

import pytest

from app.rag.evals import report
from app.rag.evals.run import run

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="no API key"),
]


@pytest.mark.parametrize("strategy", ["structural", "fixed_500"])
async def test_full_run(strategy: str) -> None:
    result = await run(strategy, offline=False)
    rows = report.evaluate_thresholds(result.summary(), report.load_thresholds("full"))
    if strategy == "structural":
        assert report.failed(rows) == []
    assert not any(p.leaked for p in result.poisoned)

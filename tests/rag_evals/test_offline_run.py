"""`--offline` end to end on the evals database (no key, no network)."""

import pytest
from sqlalchemy.exc import OperationalError

from app.rag.evals import report
from app.rag.evals.run import run


async def test_offline_run_meets_its_thresholds() -> None:
    try:
        result = await run("structural", offline=True)
    except OperationalError as exc:  # pragma: no cover
        pytest.skip(f"Postgres unavailable: {exc}")
    rows = report.evaluate_thresholds(
        result.summary(), report.load_thresholds("offline")
    )
    assert report.failed(rows) == []
    assert len(result.cases) >= 60
    assert result.poisoned and not any(p.leaked for p in result.poisoned)


async def test_fixed_strategy_is_the_same_pipeline_with_other_chunks() -> None:
    try:
        structural = await run("structural", offline=True)
        fixed = await run("fixed_500", offline=True)
    except OperationalError as exc:  # pragma: no cover
        pytest.skip(f"Postgres unavailable: {exc}")
    assert [c.id for c in structural.cases] == [c.id for c in fixed.cases]
    assert fixed.chunks < structural.chunks

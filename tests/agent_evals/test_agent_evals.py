"""The agent eval scripts and their runner (spec 13), in fake mode."""

import dataclasses
from collections import Counter

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.agent.evals import report
from app.agent.evals.fakes import fake_llm
from app.agent.evals.runner import evaluate, load_scripts
from app.core.config import get_settings
from app.rag.embeddings import FakeEmbeddings
from app.rag.evals.runner import evals_database_url

OUTCOMES = {
    "answered",
    "abstained",
    "handoff",
    "blocked",
    "ineligible_explained",
    "refund_created",
    "refund_cancelled",
    "cancelled",
}


def test_scripts_are_well_formed() -> None:
    scripts = load_scripts()
    assert len(scripts) == 30
    assert len({s.id for s in scripts}) == 30
    assert {s.expected_outcome for s in scripts} <= OUTCOMES
    # The set covers every route of the graph, not just the happy path.
    intents = Counter(s.expected_intent for s in scripts)
    assert set(intents) >= {
        "policy_question",
        "refund_status",
        "refund_request",
        "cancel_refund",
        "human_handoff",
        "out_of_scope",
        "none",
    }
    assert {"refund_created", "ineligible_explained", "abstained"} <= {
        s.expected_outcome for s in scripts
    }


async def _evals_db_reachable() -> bool:
    try:
        engine = create_async_engine(get_settings().database_url)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
    except Exception:
        return False
    return "eval" in evals_database_url()


async def test_fake_run_meets_the_thresholds() -> None:
    if not await _evals_db_reachable():
        pytest.skip("Postgres is not reachable")
    result = await evaluate(
        load_scripts(),
        mode="fake",
        embedder=FakeEmbeddings(),
        llm_factory=fake_llm,
    )
    rows = report.evaluate_thresholds(result.summary(), report.load_thresholds())
    missed = [r for r in result.results if not (r.intent_ok and r.completed)]
    assert not report.failed(rows), [(r.id, r.outcome, r.intent) for r in missed]
    assert result.summary()["unauthorized_tool_calls"] == 0


async def test_a_tool_outside_the_allowed_set_is_flagged() -> None:
    """The metric must be able to fail: strip the allowance and it counts."""
    if not await _evals_db_reachable():
        pytest.skip("Postgres is not reachable")
    script = next(s for s in load_scripts() if s.id == "hum-01-real-person")
    strict = dataclasses.replace(script, expected_tools_allowed=[])
    result = await evaluate(
        [strict], mode="fake", embedder=FakeEmbeddings(), llm_factory=fake_llm
    )
    assert result.results[0].unauthorized_tools == ["escalate_to_human"]
    assert result.summary()["unauthorized_tool_calls"] == 1

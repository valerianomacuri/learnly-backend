"""LangSmith tracing is opt-in, private and fail-open (spec 13)."""

import json
import logging
from typing import Any

import pytest
from agent_helpers import (
    converse,
    make_runtime,
    new_thread,
    scripted_llm,
    window_answer,
)
from langchain_core.tracers.base import BaseTracer
from langchain_core.tracers.schemas import Run

from app.agent.state import Intent
from app.agent.tracing import build_tracer, run_metadata, safe_scrub, scrub
from app.core.config import Settings
from app.core.permissions import RoleName
from conftest import MakeUser

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJlMTIzNDU2"


def settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "DATABASE_URL": "postgresql+psycopg://u:p@localhost/db",
        "JWT_SECRET": "x" * 32,
    }
    return Settings.model_validate({**base, **over})


def test_provider_none_builds_nothing() -> None:
    assert build_tracer(settings()) is None


def test_langsmith_requires_a_key() -> None:
    with pytest.raises(ValueError):
        settings(TRACING_PROVIDER="langsmith")


def test_langsmith_builds_a_tracer_without_network() -> None:
    tracer = build_tracer(
        settings(TRACING_PROVIDER="langsmith", LANGSMITH_API_KEY="ls-test")
    )
    assert tracer is not None
    assert tracer.project_name == "learnly-refunds"


def test_scrub_masks_pii_and_drops_secrets() -> None:
    data = {
        "messages": ["escribe a ana@mail.com", f"Bearer {JWT}"],
        "confirmation_token": "abc.def",
        "payload_hash": "deadbeef",
        "internal_note": "sospechoso de fraude",
        "nested": [{"note": "x", "ok": "hola"}],
    }
    out = json.dumps(scrub(data), ensure_ascii=False)
    for secret in ("ana@mail.com", JWT, "abc.def", "deadbeef", "fraude"):
        assert secret not in out
    assert "hola" in out


def test_safe_scrub_never_returns_raw_data_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_: Any) -> Any:
        raise RuntimeError("scrub failed")

    monkeypatch.setattr("app.agent.tracing.scrub", boom)
    assert safe_scrub({"email": "ana@mail.com"}) == {}


def test_run_metadata_carries_ids_only() -> None:
    meta = run_metadata(
        {
            "configurable": {
                "request_id": "r",
                "thread_id": "t",
                "user_id": "u",
                "role": "x",
            }
        }
    )
    assert meta == {"request_id": "r", "thread_id": "t", "user_id": "u"}


class Recorder(BaseTracer):
    def __init__(self) -> None:
        super().__init__()
        self.runs: list[Run] = []

    def _persist_run(self, run: Run) -> None:
        self.runs.append(run)


class Exploding(BaseTracer):
    def _persist_run(self, run: Run) -> None:
        raise RuntimeError("tracer down")

    def _on_chain_start(self, run: Run) -> None:
        raise RuntimeError("tracer down")


async def ask(
    runtime: Any, maker: Any, make_user: MakeUser
) -> list[tuple[str, dict[str, Any]]]:
    ann = await make_user(RoleName.CUSTOMER)
    thread = await new_thread(maker, ann.id)
    return await converse(
        runtime, ann.id, thread, "cuántos días tengo para pedir reembolso?"
    )


async def test_tracer_failure_does_not_change_the_answer(
    session_maker: Any, make_user: MakeUser, caplog: pytest.LogCaptureFixture
) -> None:
    llm = lambda: scripted_llm(Intent.POLICY_QUESTION, answers=[window_answer()])  # noqa: E731
    plain = make_runtime(session_maker, llm())
    traced = make_runtime(session_maker, llm())
    traced.tracer = Exploding()  # type: ignore[assignment]
    with caplog.at_level(logging.ERROR):
        base = await ask(plain, session_maker, make_user)
        with_tracer = await ask(traced, session_maker, make_user)
    assert [n for n, _ in with_tracer] == [n for n, _ in base]
    assert "error" not in [n for n, _ in with_tracer]


async def test_traced_runs_carry_ids_and_no_pii(
    session_maker: Any, make_user: MakeUser
) -> None:
    recorder = Recorder()
    runtime = make_runtime(
        session_maker, scripted_llm(Intent.POLICY_QUESTION, answers=[window_answer()])
    )
    runtime.tracer = recorder  # type: ignore[assignment]
    await ask(runtime, session_maker, make_user)
    assert recorder.runs, "the graph run was not traced"
    root = recorder.runs[0]
    assert root.extra and root.extra["metadata"]["request_id"]
    assert set(root.extra["metadata"]) >= {"request_id", "thread_id", "user_id"}

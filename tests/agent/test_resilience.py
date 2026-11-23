"""Circuit breaker, fallback model, degradation and token budget (spec 12)."""

import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

sys.path.insert(0, str(Path(__file__).parent))

from agent_helpers import (  # noqa: E402
    Maker,
    converse,
    final_of,
    make_runtime,
    new_thread,
    scripted_llm,
)

from app.agent.budget import BUDGET_ERROR_CODE, TokenBudget, UsageCallback  # noqa: E402
from app.agent.llm import FakeChatModel  # noqa: E402
from app.agent.resilience import (  # noqa: E402
    BreakerState,
    CircuitBreaker,
    LLMUnavailableError,
    Resilience,
    ResilientChatModel,
)
from app.agent.sse import LLM_UNAVAILABLE_CODE  # noqa: E402
from app.agent.state import Intent  # noqa: E402
from app.core.permissions import RoleName  # noqa: E402
from app.rag.retriever import PolicyRetriever  # noqa: E402
from conftest import MakeUser  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def _no_sleep(_: float) -> None:
    return None


def resilient(
    primary: FakeChatModel,
    fallback: FakeChatModel | None = None,
    *,
    threshold: int = 2,
    clock: Clock | None = None,
) -> tuple[ResilientChatModel, CircuitBreaker]:
    breaker = CircuitBreaker(threshold, reset_s=30, clock=clock or Clock())
    policy = Resilience(
        breaker, retries=1, backoff_s=0, sleep=lambda _: None, asleep=_no_sleep
    )
    model = ResilientChatModel(primary=primary, fallback=fallback, policy=policy)
    return model, breaker


def down(n: int = 10) -> FakeChatModel:
    return FakeChatModel(script=[TimeoutError("llm timeout")] * n)


async def test_breaker_opens_after_threshold_and_fails_fast() -> None:
    primary = down()
    model, breaker = resilient(primary)
    for _ in range(2):
        with pytest.raises(LLMUnavailableError):
            await model.ainvoke("hola")
    assert breaker.state is BreakerState.OPEN
    calls = len(primary.calls)
    with pytest.raises(LLMUnavailableError):
        await model.ainvoke("hola")
    assert len(primary.calls) == calls  # open: the primary is not even called


async def test_half_open_probe_closes_the_breaker() -> None:
    clock = Clock()
    primary = FakeChatModel(script=[TimeoutError("x")] * 4 + [AIMessage(content="ok")])
    model, breaker = resilient(primary, clock=clock)
    for _ in range(2):
        with pytest.raises(LLMUnavailableError):
            await model.ainvoke("hola")
    assert breaker.state is BreakerState.OPEN
    clock.now = 31
    assert breaker.state is BreakerState.HALF_OPEN
    reply = await model.ainvoke("hola")
    assert reply.content == "ok" and breaker.state is BreakerState.CLOSED


async def test_failed_probe_reopens() -> None:
    clock = Clock()
    model, breaker = resilient(down(), clock=clock)
    for _ in range(2):
        with pytest.raises(LLMUnavailableError):
            await model.ainvoke("hola")
    clock.now = 31
    with pytest.raises(LLMUnavailableError):
        await model.ainvoke("hola")
    assert breaker.state is BreakerState.OPEN


async def test_retry_recovers_without_tripping() -> None:
    primary = FakeChatModel(script=[TimeoutError("x"), AIMessage(content="ok")])
    model, breaker = resilient(primary)
    assert (await model.ainvoke("hola")).content == "ok"
    assert breaker.state is BreakerState.CLOSED and len(primary.calls) == 2


async def test_fallback_model_answers_when_primary_is_down() -> None:
    fallback = FakeChatModel(script=[AIMessage(content="from fallback")])
    model, _ = resilient(down(), fallback)
    assert (await model.ainvoke("hola")).content == "from fallback"


async def test_non_outage_errors_do_not_trip_the_breaker() -> None:
    primary = FakeChatModel(script=[ValueError("bad output")] * 5)
    model, breaker = resilient(primary, threshold=1)
    for _ in range(3):
        with pytest.raises(ValueError):
            await model.ainvoke("hola")
    assert breaker.state is BreakerState.CLOSED


class Out(BaseModel):
    x: int = 1


async def test_structured_output_goes_through_the_breaker() -> None:
    primary = FakeChatModel(structured={"Out": [TimeoutError("x")] * 4})
    fallback = FakeChatModel(structured={"Out": [Out(x=7)]})
    model, _ = resilient(primary, fallback)
    result = await model.with_structured_output(Out).ainvoke("hola")
    assert result.x == 7


async def test_chat_degrades_explicitly_when_the_llm_is_down(
    session_maker: Maker, make_user: MakeUser
) -> None:
    ann = await make_user(RoleName.CUSTOMER)
    model, breaker = resilient(down())
    breaker.force_open()
    runtime = make_runtime(session_maker, model)
    thread = await new_thread(session_maker, ann.id)
    events = await converse(
        runtime, ann.id, thread, "¿Cuántos días tengo para pedir un reembolso?"
    )
    errors = [d for name, d in events if name == "error"]
    assert errors and errors[0]["code"] == LLM_UNAVAILABLE_CODE
    assert "formulario" in errors[0]["message"]


async def test_vector_store_down_offers_a_person(
    session_maker: Maker, make_user: MakeUser
) -> None:
    ann = await make_user(RoleName.CUSTOMER)

    def broken(_: object) -> PolicyRetriever:
        raise OperationalError("select", {}, Exception("vector store down"))

    runtime = make_runtime(
        session_maker,
        scripted_llm(Intent.POLICY_QUESTION),
        retriever_factory=broken,  # type: ignore[arg-type]
    )
    thread = await new_thread(session_maker, ann.id)
    events = await converse(runtime, ann.id, thread, "¿Cuál es el plazo?")
    final = final_of(events)
    assert "no puedo consultar las políticas" in final["content"].lower()
    assert "derive" in final["content"].lower()
    assert not [d for name, d in events if name == "error"]


async def test_token_budget_cuts_the_thread(
    session_maker: Maker, make_user: MakeUser
) -> None:
    ann = await make_user(RoleName.CUSTOMER)
    budget = TokenBudget(None, limit=100)
    llm = scripted_llm([Intent.POLICY_QUESTION, Intent.POLICY_QUESTION])
    runtime = make_runtime(session_maker, llm, budget=budget)
    thread = await new_thread(session_maker, ann.id)
    await budget.add(thread, 100)
    events = await converse(runtime, ann.id, thread, "¿Cuál es el plazo?")
    assert [name for name, _ in events] == ["error"]
    assert events[0][1]["code"] == BUDGET_ERROR_CODE
    assert llm.structured_calls == [] and llm.calls == []  # the LLM was not called


async def test_usage_is_accumulated_into_the_budget() -> None:
    usage = UsageCallback()
    message = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
    )
    result = LLMResult(generations=[[ChatGeneration(message=message)]])
    usage.on_llm_end(result)
    usage.on_llm_end(result)
    assert usage.total == 24
    budget = TokenBudget(None, limit=20)
    assert not await budget.exhausted("t1")
    await budget.add("t1", usage.total)
    assert await budget.exhausted("t1")

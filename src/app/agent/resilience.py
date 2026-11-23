"""LLM resilience (spec 12): timeouts, retries with backoff, circuit breaker, fallback.

`ResilientChatModel` wraps a primary `BaseChatModel` (and an optional fallback
model, also OpenAI). Calls go primary -> retry with backoff -> fallback; after
`failure_threshold` consecutive exhausted calls the breaker opens and calls fail
fast (straight to the fallback, or `LLMUnavailableError`) until `reset_s` passes;
then one probe goes through (half-open). Business errors (bad output, 4xx) are not
outages and never trip the breaker.

`bind_tools` and `with_structured_output` delegate to both models and return a
guarded runnable, so every node keeps using the plain LangChain API.
"""

import asyncio
import enum
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

import openai
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableConfig
from pydantic import ConfigDict

logger = logging.getLogger("app.agent.resilience")


class LLMUnavailableError(Exception):
    """The LLM cannot be reached (breaker open or retries and fallback exhausted)."""


class BreakerState(enum.StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """In-memory (per process) closed / open / half-open breaker."""

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.reset_s = reset_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probing = False
        self._lock = threading.Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            return self._state()

    def _state(self) -> BreakerState:
        if self._opened_at is None:
            return BreakerState.CLOSED
        if self._clock() - self._opened_at >= self.reset_s:
            return BreakerState.HALF_OPEN
        return BreakerState.OPEN

    def allow(self) -> bool:
        """May a call go through? Half-open lets exactly one probe in."""
        with self._lock:
            state = self._state()
            if state is BreakerState.CLOSED:
                return True
            if state is BreakerState.HALF_OPEN and not self._probing:
                self._probing = True
                return True
            return False

    def success(self) -> None:
        with self._lock:
            self._failures, self._opened_at, self._probing = 0, None, False

    def failure(self) -> None:
        with self._lock:
            self._probing = False
            self._failures += 1
            if self._opened_at is not None or self._failures >= self.failure_threshold:
                if self._opened_at is None:
                    logger.error("event=llm_breaker_open failures=%d", self._failures)
                self._opened_at = self._clock()

    def force_open(self) -> None:
        with self._lock:
            self._opened_at = self._clock()
            self._probing = False

    def reset(self) -> None:
        self.success()


def is_outage(exc: BaseException) -> bool:
    """Availability errors only: parsing or 4xx errors are not outages."""
    if isinstance(exc, TimeoutError | ConnectionError | LLMUnavailableError):
        return True
    return isinstance(
        exc,
        openai.APIConnectionError
        | openai.APITimeoutError
        | openai.RateLimitError
        | openai.InternalServerError,
    )


Sleep = Callable[[float], Awaitable[None]]


class Resilience:
    """Retry/breaker/fallback policy shared by every LLM call of the process."""

    def __init__(
        self,
        breaker: CircuitBreaker | None = None,
        *,
        retries: int = 1,
        backoff_s: float = 0.2,
        sleep: Callable[[float], None] = time.sleep,
        asleep: Sleep = asyncio.sleep,
    ) -> None:
        self.breaker = breaker or CircuitBreaker()
        self.retries = retries
        self.backoff_s = backoff_s
        self._sleep = sleep
        self._asleep = asleep

    def _delay(self, attempt: int) -> float:
        return float(self.backoff_s * (2**attempt))

    def call(
        self, primary: Callable[[], Any], fallback: Callable[[], Any] | None
    ) -> Any:
        last: BaseException = LLMUnavailableError("circuit open")
        if self.breaker.allow():
            for attempt in range(self.retries + 1):
                try:
                    result = primary()
                except Exception as exc:
                    if not is_outage(exc):
                        self.breaker.success()
                        raise
                    last = exc
                    if attempt < self.retries:
                        self._sleep(self._delay(attempt))
                else:
                    self.breaker.success()
                    return result
            self.breaker.failure()
        if fallback is not None:
            try:
                logger.warning("llm primary failed (%r); using fallback", last)
                return fallback()
            except Exception as exc:
                if not is_outage(exc):
                    raise
                last = exc
        raise LLMUnavailableError(str(last) or "LLM unavailable") from last

    async def acall(
        self,
        primary: Callable[[], Awaitable[Any]],
        fallback: Callable[[], Awaitable[Any]] | None,
    ) -> Any:
        last: BaseException = LLMUnavailableError("circuit open")
        if self.breaker.allow():
            for attempt in range(self.retries + 1):
                try:
                    result = await primary()
                except Exception as exc:
                    if not is_outage(exc):
                        self.breaker.success()
                        raise
                    last = exc
                    if attempt < self.retries:
                        await self._asleep(self._delay(attempt))
                else:
                    self.breaker.success()
                    return result
            self.breaker.failure()
        if fallback is not None:
            try:
                logger.warning("llm primary failed (%r); using fallback", last)
                return await fallback()
            except Exception as exc:
                if not is_outage(exc):
                    raise
                last = exc
        raise LLMUnavailableError(str(last) or "LLM unavailable") from last


class _Guarded(Runnable[Any, Any]):
    """A bound/structured runnable of the primary model with the same guard."""

    def __init__(
        self,
        primary: Runnable[Any, Any],
        fallback: Runnable[Any, Any] | None,
        policy: Resilience,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._policy = policy

    def invoke(
        self, input: Any, config: RunnableConfig | None = None, **kwargs: Any
    ) -> Any:
        fb = self._fallback
        return self._policy.call(
            lambda: self._primary.invoke(input, config, **kwargs),
            (lambda: fb.invoke(input, config, **kwargs)) if fb else None,
        )

    async def ainvoke(
        self, input: Any, config: RunnableConfig | None = None, **kwargs: Any
    ) -> Any:
        fb = self._fallback
        return await self._policy.acall(
            lambda: self._primary.ainvoke(input, config, **kwargs),
            (lambda: fb.ainvoke(input, config, **kwargs)) if fb else None,
        )


class ResilientChatModel(BaseChatModel):
    """Primary + optional fallback model behind one breaker."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    primary: BaseChatModel
    fallback: BaseChatModel | None = None
    policy: Resilience

    @property
    def _llm_type(self) -> str:
        return f"resilient-{self.primary._llm_type}"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        fb = self.fallback
        result: ChatResult = self.policy.call(
            lambda: self.primary._generate(messages, stop, run_manager, **kwargs),
            (lambda: fb._generate(messages, stop, run_manager, **kwargs))
            if fb
            else None,
        )
        return result

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        fb = self.fallback
        result: ChatResult = await self.policy.acall(
            lambda: self.primary._agenerate(messages, stop, run_manager, **kwargs),
            (lambda: fb._agenerate(messages, stop, run_manager, **kwargs))
            if fb
            else None,
        )
        return result

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable[Any, Any]:
        fb = self.fallback
        return _Guarded(
            self.primary.bind_tools(tools, **kwargs),
            fb.bind_tools(tools, **kwargs) if fb else None,
            self.policy,
        )

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:
        fb = self.fallback
        return _Guarded(
            self.primary.with_structured_output(schema, **kwargs),
            fb.with_structured_output(schema, **kwargs) if fb else None,
            self.policy,
        )

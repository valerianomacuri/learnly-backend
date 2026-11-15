"""Chat model factory for the agent, plus a scriptable fake for tests (spec 09).

The agent needs `bind_tools` and `with_structured_output`, so it uses a LangChain
`BaseChatModel`. `rag/llm.py` keeps its own narrow port; `LangChainChat` adapts one
to the other so `PolicyAnswerer` is reused unchanged.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any, TypeVar

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.rag.embeddings import EmbeddingsUnavailableError

T = TypeVar("T", bound=BaseModel)


def get_chat_model() -> BaseChatModel:
    settings = get_settings()
    if settings.openai_api_key is None:
        raise EmbeddingsUnavailableError("OPENAI_API_KEY is not set")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.openai_api_key,
        temperature=0,
        timeout=settings.llm_timeout_s,
        max_retries=settings.llm_max_retries,
    )


class LangChainChat:
    """`rag.llm.ChatModel` over a LangChain model (sync API; callers use a thread)."""

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    def complete(self, system: str, user: str) -> str:
        reply = self._model.invoke([("system", system), ("human", user)])
        return str(reply.content)

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        runnable = self._model.with_structured_output(schema)
        result: Any = runnable.invoke([("system", system), ("human", user)])
        return result if isinstance(result, schema) else schema.model_validate(result)


class FakeChatModel(BaseChatModel):
    """Scripted model: `script` holds the replies (or exceptions) in order.

    `structured` maps a schema class name to a handler (callable on the messages), a
    list consumed in order, or a fixed object; an Exception item is raised.
    `bound_tools` records what `bind_tools` received.
    """

    script: list[AIMessage | Exception] = Field(default_factory=list)
    structured: dict[str, Any] = Field(default_factory=dict)
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    structured_calls: list[str] = Field(default_factory=list)
    bound_tools: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-scripted"

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(list(messages))
        if not self.script:
            raise AssertionError("FakeChatModel: script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        message = self._next(messages)
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=message.content, tool_calls=message.tool_calls
            )
        )

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for chunk in self._stream(messages):
            await asyncio.sleep(0)
            yield chunk

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "FakeChatModel":
        self.bound_tools = list(tools)
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:
        name = schema.__name__
        self.structured_calls.append(name)

        def run(messages: Any) -> Any:
            handler = self.structured.get(name)
            if handler is None:
                raise AssertionError(f"FakeChatModel: no structured handler {name}")
            if isinstance(handler, list):
                item = handler.pop(0)
            elif callable(handler):
                item = handler(messages)
            else:
                item = handler
            if isinstance(item, Exception):
                raise item
            return item

        return RunnableLambda(run)

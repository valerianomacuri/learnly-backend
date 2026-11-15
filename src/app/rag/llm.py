"""Chat model port (spec 07): one narrow interface, OpenAI and a scripted fake."""

from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from app.core.config import get_settings
from app.rag.embeddings import EmbeddingsUnavailableError

T = TypeVar("T", bound=BaseModel)


class ChatModel(Protocol):
    def complete(self, system: str, user: str) -> str: ...

    def structured(self, system: str, user: str, schema: type[T]) -> T: ...


class OpenAIChat:
    def __init__(self, model: str, api_key: str) -> None:
        from langchain_openai import ChatOpenAI

        self._llm = ChatOpenAI(model=model, api_key=api_key, temperature=0)  # type: ignore[arg-type]

    def complete(self, system: str, user: str) -> str:
        reply = self._llm.invoke([("system", system), ("human", user)])
        return str(reply.content)

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        runnable = self._llm.with_structured_output(schema)
        result: Any = runnable.invoke([("system", system), ("human", user)])
        return result if isinstance(result, schema) else schema.model_validate(result)


class FakeChatModel:
    """Scripted model for tests: `structured` / `complete` call the given handlers."""

    def __init__(
        self,
        structured: Callable[[str, str, type[BaseModel]], BaseModel] | None = None,
        complete: Callable[[str, str], str] | None = None,
    ) -> None:
        self._structured = structured
        self._complete = complete
        self.calls: list[tuple[str, str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append(("complete", system, user))
        if self._complete is None:
            raise AssertionError("unexpected complete() call")
        return self._complete(system, user)

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        self.calls.append((schema.__name__, system, user))
        if self._structured is None:
            raise AssertionError("unexpected structured() call")
        result = self._structured(system, user, schema)
        assert isinstance(result, schema)
        return result


def get_chat_model() -> ChatModel:
    settings = get_settings()
    if settings.openai_api_key is None:
        raise EmbeddingsUnavailableError("OPENAI_API_KEY is not set")
    return OpenAIChat(settings.llm_model, settings.openai_api_key.get_secret_value())

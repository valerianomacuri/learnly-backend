"""Runtime dependencies of the graph (closed over by the nodes, never in state)."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.principal import AgentPrincipal
from app.agent.tools.base import ToolContext
from app.rag.retriever import PolicyRetriever

T = TypeVar("T")


@dataclass(frozen=True)
class AgentDeps:
    session_maker: async_sessionmaker[AsyncSession]
    llm: BaseChatModel
    retriever_factory: Callable[[AsyncSession], PolicyRetriever]
    llm_timeout_s: float = 30.0
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    def tool_context(self, config: RunnableConfig) -> ToolContext:
        return ToolContext(
            AgentPrincipal.from_config(config),
            self.session_maker,
            self.retriever_factory,
        )

    async def timed(self, awaitable: Awaitable[T]) -> T:
        """Per-call timeout of an LLM invocation (`TimeoutError` is retryable)."""
        return await asyncio.wait_for(awaitable, self.llm_timeout_s)


def last_user_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if getattr(message, "type", "") == "human":
            return str(message.content)
    return ""

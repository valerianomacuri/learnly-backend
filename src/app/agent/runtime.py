"""`AgentRuntime`: the compiled graph plus what the API needs to run it."""

import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.deps import AgentDeps
from app.agent.graph import RECURSION_LIMIT, build_graph
from app.agent.llm import get_chat_model
from app.agent.sse import translate
from app.core.config import get_settings
from app.core.security import Principal
from app.rag.embeddings import get_embedder
from app.rag.retriever import PolicyRetriever, get_reranker
from app.schemas.chat import ChatMessageRead


@dataclass
class AgentRuntime:
    graph: CompiledStateGraph[Any, Any, Any, Any]
    session_maker: async_sessionmaker[AsyncSession]

    def config(
        self, principal: Principal, thread_id: uuid.UUID, request_id: str
    ) -> RunnableConfig:
        """Identity comes from the authenticated principal, never from the model."""
        role = sorted(principal.roles)[0] if principal.roles else ""
        return {
            "recursion_limit": RECURSION_LIMIT,
            "configurable": {
                "thread_id": str(thread_id),
                "user_id": str(principal.user_id),
                "role": role,
                "request_id": request_id,
            },
        }

    def stream(
        self, config: RunnableConfig, content: str
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        raw: AsyncIterator[tuple[str, Any]] = self.graph.astream(  # type: ignore[assignment]
            {"messages": [HumanMessage(content=content)]},
            config,
            stream_mode=["updates", "custom"],
        )
        return translate(raw)

    def resume(
        self, config: RunnableConfig, value: dict[str, Any]
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Continue a graph stopped at `interrupt()` with the customer's decision."""
        raw: AsyncIterator[tuple[str, Any]] = self.graph.astream(  # type: ignore[assignment]
            Command(resume=value), config, stream_mode=["updates", "custom"]
        )
        return translate(raw)

    async def pending_interrupt(self, config: RunnableConfig) -> dict[str, Any] | None:
        """The card of the interrupt the thread is waiting on (survives a restart)."""
        snapshot = await self.graph.aget_state(config)
        for task in snapshot.tasks:
            for pending in task.interrupts:
                return dict(pending.value)
        return None

    async def history(self, config: RunnableConfig) -> list[ChatMessageRead]:
        snapshot = await self.graph.aget_state(config)
        messages: list[AnyMessage] = snapshot.values.get("messages", [])
        history: list[ChatMessageRead] = []
        for message in messages:
            if message.type == "human":
                history.append(
                    ChatMessageRead(role="user", content=str(message.content))
                )
            elif message.type == "ai" and message.content:
                history.append(
                    ChatMessageRead(
                        role="assistant",
                        content=str(message.content),
                        citations=message.additional_kwargs.get("citations", []),
                    )
                )
        return history


async def start_runtime(
    session_maker: async_sessionmaker[AsyncSession], stack: AsyncExitStack
) -> AgentRuntime | None:
    """Production wiring; `None` (chat answers 503) without an OpenAI key."""
    settings = get_settings()
    if settings.openai_api_key is None:
        return None
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    conninfo = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    pool = AsyncConnectionPool(
        conninfo,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    stack.push_async_callback(pool.close)
    checkpointer = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
    await checkpointer.setup()  # creates the saver's tables (idempotent)

    embedder = get_embedder()
    reranker = get_reranker()

    def retriever_factory(session: AsyncSession) -> PolicyRetriever:
        return PolicyRetriever(session, embedder, reranker=reranker)

    deps = AgentDeps(
        session_maker=session_maker,
        llm=get_chat_model(),
        retriever_factory=retriever_factory,
        llm_timeout_s=settings.llm_timeout_s,
    )
    return AgentRuntime(build_graph(deps, checkpointer), session_maker)

"""Manual check against the real OpenAI API: `uv run pytest -m live tests/agent`."""

import pytest
from agent_helpers import converse, final_of, new_thread
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.deps import AgentDeps
from app.agent.graph import build_graph
from app.agent.llm import get_chat_model
from app.agent.runtime import AgentRuntime
from app.rag.embeddings import get_embedder
from app.rag.ingest import ingest
from app.rag.retriever import PolicyRetriever
from conftest import POLICIES_DIR, MakeUser

pytestmark = pytest.mark.live


async def test_policy_question_with_the_real_model(
    session_maker: async_sessionmaker[AsyncSession],
    make_user: MakeUser,
) -> None:
    embedder = get_embedder()
    async with session_maker() as session:  # real embeddings, like production
        await ingest(session, POLICIES_DIR, embedder)
        await session.commit()
    deps = AgentDeps(
        session_maker,
        get_chat_model(),
        lambda s: PolicyRetriever(s, embedder),
    )
    runtime = AgentRuntime(build_graph(deps, InMemorySaver()), session_maker)
    ann = await make_user()
    thread = await new_thread(session_maker, ann.id)

    events = await converse(
        runtime, ann.id, thread, "¿Cuántos días tengo para pedir un reembolso?"
    )

    final = final_of(events)
    assert final["citations"]
    assert "30" in final["content"]

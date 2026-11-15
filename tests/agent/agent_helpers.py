"""Helpers of the agent tests (no conftest here: it would shadow the root one)."""

import uuid
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.deps import AgentDeps
from app.agent.graph import build_graph
from app.agent.llm import FakeChatModel
from app.agent.nodes.classify_intent import IntentResult
from app.agent.runtime import AgentRuntime
from app.agent.state import Intent
from app.core.permissions import RoleName
from app.core.security import Principal
from app.models.chat import ChatThread
from app.rag.answer import GroundingVerdict, PolicyAnswer
from app.rag.embeddings import FakeEmbeddings
from app.rag.retriever import PolicyRetriever
from app.schemas.refund import Citation

Maker = async_sessionmaker[AsyncSession]
WINDOW = Citation(doc_key="refund-eligibility", section_path="Ventana de 30 días")
Events = list[tuple[str, dict[str, Any]]]


def make_runtime(
    maker: Maker, llm: FakeChatModel, *, threshold: float = 0.2
) -> AgentRuntime:
    def retriever_factory(session: AsyncSession) -> PolicyRetriever:
        return PolicyRetriever(session, FakeEmbeddings(), threshold=threshold)

    deps = AgentDeps(maker, llm, retriever_factory, llm_timeout_s=5)
    return AgentRuntime(build_graph(deps, InMemorySaver()), maker)


def scripted_llm(
    intent: Intent | Exception | list[Intent | Exception],
    *,
    answers: list[PolicyAnswer] | None = None,
    script: list[AIMessage | Exception] | None = None,
    supported: bool = True,
) -> FakeChatModel:
    intents = intent if isinstance(intent, list) else [intent]
    return FakeChatModel(
        script=list(script or []),
        structured={
            "IntentResult": [
                i if isinstance(i, Exception) else IntentResult(intent=i)
                for i in intents
            ],
            "PolicyAnswer": list(answers or []),
            "GroundingVerdict": GroundingVerdict(supported=supported),
        },
    )


def window_answer(text: str = "Tienes 30 días.") -> PolicyAnswer:
    return PolicyAnswer(answer=text, citations=[WINDOW], needs_handoff=False)


async def new_thread(maker: Maker, user_id: uuid.UUID) -> uuid.UUID:
    async with maker() as session:
        thread = ChatThread(customer_id=user_id)
        session.add(thread)
        await session.commit()
        return thread.id


async def converse(
    runtime: AgentRuntime, user_id: uuid.UUID, thread_id: uuid.UUID, text: str
) -> Events:
    principal = Principal.for_roles(user_id, [RoleName.CUSTOMER])
    config = runtime.config(principal, thread_id, "trace-test")
    return [event async for event in runtime.stream(config, text)]


def final_of(events: Events) -> dict[str, Any]:
    finals = [data for name, data in events if name == "final"]
    assert len(finals) == 1, events
    return finals[0]

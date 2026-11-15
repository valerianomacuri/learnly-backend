"""Manual checks with the real OpenAI API: `pytest -m live` (OPENAI_API_KEY)."""

import os

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.rag.answer import PolicyAnswerer
from app.rag.embeddings import get_embedder
from app.rag.ingest import ingest
from app.rag.llm import get_chat_model
from app.rag.retriever import PolicyRetriever
from app.repositories.policy import PolicyRepository
from conftest import POLICIES_DIR

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="no API key"),
]


async def test_window_question_and_out_of_scope(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    embedder, llm = get_embedder(), get_chat_model()
    async with session_maker() as session:
        await ingest(session, POLICIES_DIR, embedder)
        await session.commit()
    async with session_maker() as session:
        config = await PolicyRepository(session).get_active_config()
        answerer = PolicyAnswerer(
            PolicyRetriever(session, embedder, llm=llm), llm, config
        )
        window = await answerer.answer("¿Cuántos días tengo para pedir un reembolso?")
        assert "30" in window.answer.answer and not window.answer.needs_handoff
        assert any(
            c.section_path == "Elegibilidad > Ventana de 30 días"
            for c in window.answer.citations
        )
        away = await answerer.answer("¿Cuál es la capital de Francia?")
        assert away.answer.needs_handoff

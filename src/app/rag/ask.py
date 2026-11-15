"""`python -m app.rag.ask "<question>"`: answer, citations and retrieval trace."""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.domain.policy_config import PolicyConfigError
from app.rag.answer import AnswerResult, PolicyAnswerer
from app.rag.embeddings import EmbeddingsUnavailableError, get_embedder
from app.rag.llm import get_chat_model
from app.rag.retriever import PolicyRetriever, get_reranker
from app.repositories.policy import PolicyRepository


async def _ask(question: str, history: tuple[str, ...]) -> AnswerResult:
    embedder = get_embedder()
    llm = get_chat_model()
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            config = await PolicyRepository(session).get_active_config()
            retriever = PolicyRetriever(
                session, embedder, llm=llm, reranker=get_reranker()
            )
            return await PolicyAnswerer(retriever, llm, config).answer(
                question, history=history
            )
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rag.ask", description="Ask the policies.")
    parser.add_argument("question")
    parser.add_argument(
        "--history", action="append", default=[], help="previous turn (repeatable)"
    )
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(
            _ask(args.question, tuple(args.history)),
            loop_factory=asyncio.SelectorEventLoop,
        )
    except (EmbeddingsUnavailableError, PolicyConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    answer = result.answer
    print(answer.answer)
    print(f"\nneeds_handoff: {answer.needs_handoff}")
    for citation in answer.citations:
        print(f"  cita: [{citation.doc_key}] {citation.section_path}")
    print(f"\nquery: {result.retrieval.query!r} (lang={result.retrieval.language})")
    print(f"best_score: {result.retrieval.best_score:.3f}")
    for chunk in result.retrieval.chunks:
        print(
            f"  - {chunk.doc_key} | {chunk.section_path} "
            f"(sim={chunk.similarity:.3f}, score={chunk.score:.4f})"
        )
    for line in result.trace:
        print(f"  trace: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

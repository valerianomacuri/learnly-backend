"""Offline stand-ins for the LLM (spec 08 `--offline`): no network, no key."""

import re

from pydantic import BaseModel

from app.rag.answer import GroundingVerdict, PolicyAnswer
from app.rag.llm import ChatModel
from app.schemas.refund import Citation

_CHUNK = re.compile(r'<chunk doc="(.*?)" section="(.*?)">\n(.*?)\n</chunk>', re.DOTALL)
_MARKDOWN = re.compile(r"[#*|>`-]+|\n+")
MAX_CHARS = 300


class ExtractiveChatModel:
    """Answers with the top retrieved chunk, quoted. It never invents anything.

    Good enough to exercise retrieval, citations and the deterministic figure check
    without a model; the grounding verdict is always "supported" (the real RAGAS
    judge runs only in the full mode).
    """

    def complete(self, system: str, user: str) -> str:
        return user.rsplit("Question:", 1)[-1].strip()

    def structured[T: BaseModel](self, system: str, user: str, schema: type[T]) -> T:
        if schema is GroundingVerdict:
            return schema.model_validate({"supported": True})
        match = _CHUNK.search(user)
        if match is None:
            return schema.model_validate(
                {"answer": "", "citations": [], "needs_handoff": True}
            )
        doc_key, section, body = match.groups()
        text = " ".join(_MARKDOWN.sub(" ", body).split())[:MAX_CHARS]
        return schema.model_validate(
            PolicyAnswer(
                answer=text,
                citations=[Citation(doc_key=doc_key, section_path=section)],
                needs_handoff=False,
            ).model_dump()
        )


def offline_chat() -> ChatModel:
    return ExtractiveChatModel()

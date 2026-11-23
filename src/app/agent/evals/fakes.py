"""Scripted LLM for `--fake` agent evals (no key, no network).

The model is deterministic and does the same job the keyword fallback does: the
intent comes from `classify_by_keywords`, so the intent metric measures real code
(routing keywords and graph wiring), not the script's own answer. Everything the
flow extracts (purchase, reason, which refund to cancel) comes from the script's
ground truth, as a perfectly cooperative model would return it.
"""

import re
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage

from app.agent.guardrails.injection import InjectionVerdict
from app.agent.llm import FakeChatModel
from app.agent.nodes.cancel_refund import CancelChoice
from app.agent.nodes.classify_intent import IntentResult
from app.agent.nodes.collect_refund_info import CollectResult
from app.agent.nodes.keyword_classifier import classify_by_keywords
from app.rag.answer import GroundingVerdict, PolicyAnswer
from app.schemas.refund import Citation

DEFAULT_REASON = "El contenido del curso no corresponde con la descripción publicada."


@dataclass(frozen=True, slots=True)
class Facts:
    """Ground truth a script refers to (resolved against the seeded database)."""

    purchase_id: str | None = None
    refund_id: str | None = None
    reason: str = DEFAULT_REASON
    status_tools: tuple[str, ...] = ()


_CHUNK = re.compile(r'<chunk doc="(.*?)" section="(.*?)">')


def cite_first_chunk(messages: Any) -> PolicyAnswer:
    """Answer from the retrieved context: cite its first chunk, state no figures.

    The citation is always one the retriever really returned, so the answerer's
    citation and figure checks run for real against what retrieval found.
    """
    text = "\n".join(str(getattr(m, "content", m)) for m in messages)
    match = _CHUNK.search(text)
    if match is None:
        return PolicyAnswer(answer="", citations=[], needs_handoff=True)
    doc_key, section = match.groups()
    return PolicyAnswer(
        answer="Esto es lo que indica la política vigente sobre tu consulta.",
        citations=[Citation(doc_key=doc_key, section_path=section)],
        needs_handoff=False,
    )


def fake_llm(facts: Facts) -> FakeChatModel:
    def classify(messages: Any) -> IntentResult:
        return IntentResult(intent=classify_by_keywords(str(messages[-1].content)))

    def collect(_: Any) -> CollectResult:
        return CollectResult(
            purchase_id=facts.purchase_id,
            reason_category="NOT_AS_DESCRIBED",
            reason_text=facts.reason,
        )

    script: list[Any] = []
    if facts.status_tools:
        script.append(
            AIMessage(
                content="",
                tool_calls=[
                    {"name": name, "args": {}, "id": f"c{i}"}
                    for i, name in enumerate(facts.status_tools)
                ],
            )
        )
        script.append(AIMessage(content="Esto es lo que encontré sobre tu consulta."))
    # Free-text narration (explanations, questions to the customer) after the above.
    script += [AIMessage(content="Con gusto te ayudo con eso.") for _ in range(20)]
    return FakeChatModel(
        script=script,
        structured={
            "IntentResult": classify,
            "InjectionVerdict": InjectionVerdict(is_injection=False),
            "PolicyAnswer": cite_first_chunk,
            "GroundingVerdict": GroundingVerdict(supported=True),
            "CollectResult": collect,
            "CancelChoice": CancelChoice(refund_id=facts.refund_id),
        },
    )

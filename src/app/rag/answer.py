"""Grounded policy answers with verified citations (spec 07).

Flow: retrieve -> (gate) -> generate structured answer -> keep only citations that
point to retrieved chunks -> deterministic number check (regenerate once, else
abstain) -> LLM grounding judge (unsupported -> abstain).

Number check: every `N días` / `N %` in the answer must be one of the policy
parameters (`PolicyConfig`) or appear literally in the retrieved context (the
corpus also states figures that are not parameters, e.g. "10 días hábiles").
"""

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from pydantic import BaseModel

from app.domain.policy_config import PolicyConfig
from app.rag.llm import ChatModel
from app.rag.reason_map import PATH_SEPARATOR, doc_label, unqualify
from app.rag.retriever import PolicyRetriever, RetrievalResult, RetrievedChunk
from app.schemas.refund import Citation

PROMPTS_DIR = Path(__file__).parent / "prompts"
ANSWER_PROMPT = "policy_answer_v1.md"
JUDGE_PROMPT = "grounding_judge_v1.md"

_NUMBER = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(%|por ciento|d[ií]as?|days?)", re.IGNORECASE
)
ABSTENTION = {
    "es": (
        "No encuentro información suficiente en las políticas para responder "
        "eso. Te derivo con una persona del equipo."
    ),
    "en": (
        "I couldn't find enough information in the policies to answer that. "
        "I'll hand you over to a team member."
    ),
}


class PolicyAnswer(BaseModel):
    answer: str
    citations: list[Citation]
    needs_handoff: bool


class GroundingVerdict(BaseModel):
    supported: bool
    unsupported_claims: list[str] = []


@dataclass(slots=True)
class AnswerResult:
    answer: PolicyAnswer
    retrieval: RetrievalResult
    trace: list[str] = field(default_factory=list)


@cache
def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _normalize(number: str) -> str:
    number = number.replace(",", ".")
    return number.rstrip("0").rstrip(".") if "." in number else number


def _numbers(text: str) -> set[str]:
    return {_normalize(m.group(1)) for m in _NUMBER.finditer(text)}


def allowed_numbers(
    config: PolicyConfig,
    chunks: list[RetrievedChunk],
    trusted: frozenset[str] | None = None,
) -> set[str]:
    """Figures an answer may state.

    With `trusted` (the front-matter parameters of the active documents) only those
    and `PolicyConfig` count: a figure that merely appears in a retrieved chunk is
    not enough, so a poisoned document cannot launder "90 días" (spec 08). Without
    it, literal figures of the retrieved context are accepted too (spec 07).
    """
    allowed = {
        str(value)
        for value in config.model_dump().values()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    if trusted is not None:
        return allowed | set(trusted)
    for chunk in chunks:
        allowed |= {
            _normalize(n.group(0)) for n in re.finditer(r"\d+(?:\.\d+)?", chunk.content)
        }
    return allowed


def unverified_numbers(
    answer: str,
    config: PolicyConfig,
    chunks: list[RetrievedChunk],
    trusted: frozenset[str] | None = None,
) -> list[str]:
    """Day/percentage figures of `answer` that the policy does not support."""
    allowed = allowed_numbers(config, chunks, trusted)
    return sorted(n for n in _numbers(answer) if n not in allowed)


def _context(chunks: list[RetrievedChunk]) -> str:
    blocks: list[str] = []
    seen_parents: set[object] = set()
    for chunk in chunks:
        body = chunk.content
        if chunk.parent_id is not None and chunk.parent_id not in seen_parents:
            seen_parents.add(chunk.parent_id)
            body = f"{chunk.parent_content}\n\n{body}"
        blocks.append(
            f'<chunk doc="{chunk.doc_key}" section="{chunk.section_path}">\n'
            f"{body}\n</chunk>"
        )
    return "<context>\n" + "\n".join(blocks) + "\n</context>"


class PolicyAnswerer:
    def __init__(
        self,
        retriever: PolicyRetriever,
        llm: ChatModel,
        config: PolicyConfig,
        trusted_figures: frozenset[str] | None = None,
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.config = config
        self.trusted_figures = trusted_figures

    def _abstain(self, language: str) -> PolicyAnswer:
        return PolicyAnswer(
            answer=ABSTENTION.get(language, ABSTENTION["es"]),
            citations=[],
            needs_handoff=True,
        )

    def _valid_citations(
        self, citations: list[Citation], chunks: list[RetrievedChunk]
    ) -> list[Citation]:
        """Keep citations that match a retrieved chunk; qualify them with the label."""
        by_key = {(c.doc_key, c.section_path): c for c in chunks}
        valid: list[Citation] = []
        for citation in citations:
            chunk = by_key.get((citation.doc_key, citation.section_path)) or by_key.get(
                (citation.doc_key, unqualify(citation.section_path))
            )
            if chunk is None:
                continue
            label = doc_label(self.config, chunk.doc_key, chunk.doc_title)
            qualified = f"{label}{PATH_SEPARATOR}{chunk.section_path}"
            item = Citation(doc_key=chunk.doc_key, section_path=qualified)
            if item not in valid:
                valid.append(item)
        return valid

    def _generate(
        self, question: str, retrieval: RetrievalResult, note: str
    ) -> PolicyAnswer:
        system = load_prompt(ANSWER_PROMPT).replace("{language}", retrieval.language)
        user = f"{_context(retrieval.chunks)}\n\nPregunta del cliente: {question}"
        if note:
            user += f"\n\n{note}"
        return self.llm.structured(system, user, PolicyAnswer)

    def _grounded(self, draft: PolicyAnswer, chunks: list[RetrievedChunk]) -> bool:
        cited = {(c.doc_key, c.section_path) for c in draft.citations}
        evidence = [
            c
            for c in chunks
            if (c.doc_key, c.section_path) in cited
            or (c.doc_key, unqualify(c.section_path)) in cited
            or any(k == c.doc_key and unqualify(p) == c.section_path for k, p in cited)
        ]
        fragments = "\n---\n".join(
            f"[{c.doc_key} | {c.section_path}]\n{c.content}" for c in evidence
        )
        verdict = self.llm.structured(
            load_prompt(JUDGE_PROMPT),
            f"RESPUESTA:\n{draft.answer}\n\nFRAGMENTOS:\n{fragments}",
            GroundingVerdict,
        )
        return verdict.supported

    async def answer(
        self, question: str, *, history: tuple[str, ...] = ()
    ) -> AnswerResult:
        retrieval = await self.retriever.retrieve(question, history=history)
        trace = list(retrieval.trace)
        language = retrieval.language
        if retrieval.insufficient_evidence:
            trace.append("abstain: insufficient evidence")
            return AnswerResult(self._abstain(language), retrieval, trace)

        draft = self._generate(question, retrieval, "")
        for attempt in (1, 2):
            bad = unverified_numbers(
                draft.answer, self.config, retrieval.chunks, self.trusted_figures
            )
            if not bad:
                break
            trace.append(f"numbers: unsupported {bad} (attempt {attempt})")
            if attempt == 2:
                return AnswerResult(self._abstain(language), retrieval, trace)
            draft = self._generate(
                question,
                retrieval,
                "Tu respuesta anterior incluía cifras que no están en el contexto: "
                f"{', '.join(bad)}. Usa solo las cifras del contexto.",
            )

        if draft.needs_handoff:
            trace.append("handoff requested by the model")
            return AnswerResult(
                PolicyAnswer(answer=draft.answer, citations=[], needs_handoff=True),
                retrieval,
                trace,
            )
        draft.citations = self._valid_citations(draft.citations, retrieval.chunks)
        if not draft.citations:
            trace.append("abstain: no valid citation")
            return AnswerResult(self._abstain(language), retrieval, trace)
        if not self._grounded(draft, retrieval.chunks):
            trace.append("abstain: grounding check failed")
            return AnswerResult(self._abstain(language), retrieval, trace)
        trace.append("verified")
        return AnswerResult(draft, retrieval, trace)

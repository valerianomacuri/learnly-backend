"""Golden set of the RAG evals (spec 08): `tests/rag_evals/golden.jsonl`."""

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.policy.loader import load_policy_directory
from app.rag.chunking import ChunkSource, get_chunker

PROJECT_ROOT = Path(__file__).resolve().parents[4]
GOLDEN_PATH = PROJECT_ROOT / "tests" / "rag_evals" / "golden.jsonl"
POISONED_DIR = PROJECT_ROOT / "tests" / "rag_evals" / "poisoned"

CATEGORIES = frozenset(
    {
        "ventana",
        "consumo",
        "certificado",
        "metodos",
        "regalos_creditos",
        "suscripciones",
        "terceros",
        "proceso",
        "estados_plazos",
        "faq",
        "adversarial",
    }
)


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    lang: Literal["es", "en"]
    question: str
    expected_sections: list[str]
    expected_answer_facts: list[str]
    expected_numbers: dict[str, int]
    should_abstain: bool
    category: str

    @model_validator(mode="after")
    def _coherent(self) -> "GoldenCase":
        if self.category not in CATEGORIES:
            raise ValueError(f"{self.id}: unknown category {self.category!r}")
        if self.should_abstain and self.expected_sections:
            raise ValueError(f"{self.id}: an abstention has no expected sections")
        if not self.should_abstain and not self.expected_sections:
            raise ValueError(f"{self.id}: needs at least one expected section")
        return self


ALTERNATIVE_SEPARATOR = " || "


def alternatives(entry: str) -> list[str]:
    """An `expected_sections` entry may list equivalent sections: `A || B`.

    Used where the corpus states the same rule twice (rule section and FAQ pair);
    covering any of them counts as retrieving the entry.
    """
    return entry.split(ALTERNATIVE_SEPARATOR)


def load_golden(path: Path = GOLDEN_PATH) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                cases.append(GoldenCase.model_validate(json.loads(line)))
            except ValueError as exc:
                raise ValueError(f"{path.name}:{number}: {exc}") from exc
    ids = Counter(c.id for c in cases)
    duplicated = [i for i, n in ids.items() if n > 1]
    if duplicated:
        raise ValueError(f"duplicated golden ids: {duplicated}")
    return cases


def section_texts(policies_dir: Path) -> Mapping[str, str]:
    """`section_path` -> section text, from the structural chunking of the corpus.

    Paths are unique across the corpus (a test guards it), so the golden set can
    name a section without its document.
    """
    chunker = get_chunker("structural")
    texts: dict[str, str] = {}
    for file in load_policy_directory(policies_dir):
        source = ChunkSource(
            file.meta.doc_key, file.meta.title, file.content, tuple(file.meta.tags)
        )
        for draft in chunker.split(source):
            texts[draft.section_path] = (
                f"{texts[draft.section_path]}\n{draft.content}"
                if draft.section_path in texts
                else draft.content
            )
    return texts

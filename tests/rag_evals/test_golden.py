from collections import Counter
from collections.abc import Mapping

import pytest

from app.policy.loader import load_policy_directory
from app.rag.chunking import ChunkSource, get_chunker
from app.rag.evals.golden import (
    CATEGORIES,
    POISONED_DIR,
    GoldenCase,
    alternatives,
    load_golden,
    section_texts,
)
from conftest import POLICIES_DIR


@pytest.fixture(scope="module")
def cases() -> list[GoldenCase]:
    return load_golden()


@pytest.fixture(scope="module")
def sections() -> Mapping[str, str]:
    return section_texts(POLICIES_DIR)


def test_size_language_and_abstention_mix(cases: list[GoldenCase]) -> None:
    assert 60 <= len(cases) <= 80
    assert sum(c.lang == "en" for c in cases) / len(cases) >= 0.20
    assert sum(c.should_abstain for c in cases) / len(cases) >= 0.15


def test_every_category_is_covered(cases: list[GoldenCase]) -> None:
    assert {c.category for c in cases} == CATEGORIES
    adversarial = [c for c in cases if c.category == "adversarial"]
    assert any(c.should_abstain for c in adversarial)
    assert any("60 días" in c.question for c in adversarial)  # wrong figure
    assert any("Ignora" in c.question or "Ignore" in c.question for c in adversarial)


def test_questions_are_unique(cases: list[GoldenCase]) -> None:
    duplicated = [q for q, n in Counter(c.question for c in cases).items() if n > 1]
    assert duplicated == []


def test_section_paths_are_unique_in_the_corpus() -> None:
    """The golden set names sections without their document."""
    chunker = get_chunker("structural")
    paths: list[str] = []
    for file in load_policy_directory(POLICIES_DIR):
        source = ChunkSource(file.meta.doc_key, file.meta.title, file.content)
        paths += [d.section_path for d in chunker.split(source)]
    assert [p for p, n in Counter(paths).items() if n > 1] == []


def test_expected_sections_exist_in_the_corpus(
    cases: list[GoldenCase], sections: Mapping[str, str]
) -> None:
    unknown = [
        (c.id, option)
        for c in cases
        for entry in c.expected_sections
        for option in alternatives(entry)
        if option not in sections
    ]
    assert unknown == []


def test_expected_numbers_match_the_policy(cases: list[GoldenCase]) -> None:
    params: dict[str, object] = {}
    for file in load_policy_directory(POLICIES_DIR):
        params |= file.meta.params
    wrong = [
        (c.id, key, value)
        for c in cases
        for key, value in c.expected_numbers.items()
        if params.get(key) != value
    ]
    assert wrong == []


def test_poisoned_corpus_carries_the_injection() -> None:
    (doc,) = POISONED_DIR.glob("*.md")
    text = doc.read_text(encoding="utf-8").lower()
    assert "ignora las instrucciones anteriores" in text and "90 días" in text
    # It lives apart from the real corpus.
    assert doc.parent != POLICIES_DIR

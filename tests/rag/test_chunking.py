import pytest

from app.policy.loader import PolicyDocumentFile, load_policy_directory
from app.rag.chunking import (
    CHUNK_TYPES,
    ChunkSource,
    FixedChunker,
    StructuralChunker,
    count_tokens,
    get_chunker,
)
from conftest import POLICIES_DIR

FILES = load_policy_directory(POLICIES_DIR)


def source(file: PolicyDocumentFile) -> ChunkSource:
    return ChunkSource(
        file.meta.doc_key, file.meta.title, file.content, tuple(file.meta.tags)
    )


@pytest.mark.parametrize("file", FILES, ids=lambda f: f.meta.doc_key)
def test_prefix_types_and_section_paths(file: PolicyDocumentFile) -> None:
    chunks = StructuralChunker().split(source(file))
    assert chunks
    for chunk in chunks:
        assert chunk.chunk_type in CHUNK_TYPES
        assert chunk.embedded_text.startswith(f"[{file.meta.title}")
        assert chunk.embedded_text.endswith(chunk.content)
        assert chunk.section_path
        assert chunk.tags == tuple(file.meta.tags)


def test_section_paths_of_eligibility() -> None:
    eligibility = next(f for f in FILES if f.meta.doc_key == "refund-eligibility")
    paths = {c.section_path for c in StructuralChunker().split(source(eligibility))}
    assert "Ventana de 30 días" in paths
    assert "Compras no elegibles > Suscripciones" in paths


def test_faq_has_one_chunk_per_question() -> None:
    faq = next(f for f in FILES if f.meta.doc_key == "refund-faq")
    questions = [ln for ln in faq.content.splitlines() if ln.startswith("### ")]
    chunks = StructuralChunker().split(source(faq))
    assert len(chunks) == len(questions)
    assert {c.chunk_type for c in chunks} == {"faq"}
    for chunk, question in zip(chunks, questions, strict=True):
        assert chunk.section_path.endswith(question.removeprefix("### "))


def test_tables_are_never_split() -> None:
    status = next(f for f in FILES if f.meta.doc_key == "refund-status-and-timelines")
    table_lines = [ln for ln in status.content.splitlines() if ln.startswith("|")]
    assert table_lines
    chunks = [c for c in StructuralChunker().split(source(status)) if "|" in c.content]
    assert len(chunks) == 1
    assert all(ln in chunks[0].content for ln in table_lines)
    assert chunks[0].chunk_type == "table"


def test_oversize_section_splits_prose_but_keeps_lists_and_tables_whole() -> None:
    prose = " ".join(["Una frase de relleno sobre reembolsos."] * 120)
    table = "| a | b |\n|---|---|\n" + "\n".join(f"| {i} | x |" for i in range(40))
    steps = "\n".join(f"{i}. Paso número {i} del procedimiento." for i in range(1, 40))
    body = f"# Doc\n\n## Larga\n\n{prose}\n\n{table}\n\n{steps}\n"
    chunks = StructuralChunker(max_tokens=300, overlap=30).split(
        ChunkSource("x", "Doc", body)
    )
    assert len(chunks) > 1
    for block in (table, steps):
        assert sum(block in c.content for c in chunks) == 1
    assert all(c.section_path == "Larga" for c in chunks)
    assert all("part" in c.metadata for c in chunks)


def test_child_points_to_parent_with_own_body() -> None:
    body = "# D\n\n## Padre\n\nTexto del padre.\n\n### Hijo\n\nTexto del hijo.\n"
    chunks = StructuralChunker().split(ChunkSource("x", "D", body))
    by_path = {c.section_path: i for i, c in enumerate(chunks)}
    assert chunks[by_path["Padre > Hijo"]].parent_index == by_path["Padre"]
    assert chunks[by_path["Padre"]].parent_index is None


def test_fixed_strategy_and_registry() -> None:
    chunks = FixedChunker(size=60, overlap=5).split(source(FILES[0]))
    assert len(chunks) > 1
    assert all(c.embedded_text == c.content for c in chunks)
    assert get_chunker("fixed_500").name == "fixed_500"
    with pytest.raises(ValueError):
        get_chunker("nope")


def test_count_tokens() -> None:
    assert count_tokens("") == 0
    assert count_tokens("hola mundo") >= 2

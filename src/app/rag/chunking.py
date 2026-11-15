"""Structural chunking of the policy Markdown (spec 07).

A chunk is a heading section (H1-H3). Tables, lists and FAQ question/answer pairs
are atomic: only prose blocks of a section above `MAX_TOKENS` are subdivided.
`section_path` drops the H1 (the document title) so it matches the
`reason_code_map` values; the contextual prefix `[title > section_path]` carries
the title in `embedded_text`.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from langchain_text_splitters import RecursiveCharacterTextSplitter

MAX_TOKENS = 800
OVERLAP_TOKENS = 80
FIXED_TOKENS = 500

CHUNK_TYPES = ("rule", "procedure", "faq", "table", "definition")
PATH_SEPARATOR = " > "

_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+")
_BULLET = re.compile(r"^\s*[-*+]\s+")
_WORD = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_DEFINITION = re.compile(r"^(qué es|definici|glosario)", re.IGNORECASE)


def count_tokens(text: str) -> int:
    """Offline token estimate (no tokenizer download): ~1.3 tokens per word piece."""
    if not text.strip():
        return 0
    return max(1, round(len(_WORD.findall(text)) * 1.3))


@dataclass(frozen=True, slots=True)
class ChunkSource:
    """A document as the chunker sees it."""

    doc_key: str
    title: str
    body: str  # Markdown without front-matter
    tags: tuple[str, ...] = ()


@dataclass(slots=True)
class ChunkDraft:
    """A chunk before persistence. `parent_index` points into the same list."""

    section_path: str
    heading_level: int
    chunk_type: str
    content: str
    embedded_text: str
    token_count: int
    source_lines: str
    tags: tuple[str, ...] = ()
    parent_index: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class Chunker(Protocol):
    name: str

    def split(self, source: ChunkSource) -> list[ChunkDraft]: ...


@dataclass(slots=True)
class _Section:
    level: int
    heading: str
    path_parts: tuple[str, ...]
    start: int  # 1-based line of the heading
    lines: list[str] = field(default_factory=list)  # body lines

    @property
    def end(self) -> int:
        return self.start + len(self.lines)


def _sections(body: str) -> list[_Section]:
    sections: list[_Section] = []
    trail: dict[int, str] = {}
    in_fence = False
    for number, line in enumerate(body.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match is None:
            if sections:
                sections[-1].lines.append(line)
            continue
        level = len(match.group(1))
        heading = match.group(2).strip()
        trail = {k: v for k, v in trail.items() if k < level}
        trail[level] = heading
        parts = tuple(v for k, v in sorted(trail.items()) if k >= 2)
        sections.append(_Section(level, heading, parts, number))
    return sections


def _blocks(lines: list[str]) -> list[str]:
    """Paragraph blocks; a table or a list is one block (never split)."""
    blocks: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return blocks


def _is_atomic(block: str) -> bool:
    lines = block.splitlines()
    if any(ln.lstrip().startswith("|") for ln in lines):
        return True
    return all(_NUMBERED.match(ln) or _BULLET.match(ln) for ln in lines)


def _classify(doc_key: str, section: _Section, text: str) -> str:
    lines = text.splitlines()
    if doc_key.endswith("faq") and section.level == 3:
        return "faq"
    if any(ln.lstrip().startswith("|") for ln in lines):
        return "table"
    if any(_NUMBERED.match(ln) for ln in lines):
        return "procedure"
    if _DEFINITION.match(section.heading) and not any(_BULLET.match(x) for x in lines):
        return "definition"
    return "rule"


def _prefix(title: str, path: str) -> str:
    return f"[{title}]" if path == title else f"[{title}{PATH_SEPARATOR}{path}]"


def _pack(blocks: list[str], limit: int, overlap: int) -> list[str]:
    """Greedy packing of blocks under `limit`; oversize prose is subdivided."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=limit, chunk_overlap=overlap, length_function=count_tokens
    )
    pieces: list[str] = []
    for block in blocks:
        if count_tokens(block) > limit and not _is_atomic(block):
            pieces.extend(splitter.split_text(block))
        else:
            pieces.append(block)
    packed: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n\n{piece}" if current else piece
        if current and count_tokens(candidate) > limit:
            packed.append(current)
            current = piece
        else:
            current = candidate
    if current:
        packed.append(current)
    return packed


class StructuralChunker:
    name = "structural"

    def __init__(
        self, max_tokens: int = MAX_TOKENS, overlap: int = OVERLAP_TOKENS
    ) -> None:
        self.max_tokens = max_tokens
        self.overlap = overlap

    def split(self, source: ChunkSource) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        # level -> index of the chunk of the nearest ancestor that has its own body
        ancestors: dict[int, int] = {}
        for section in _sections(source.body):
            text = "\n".join(section.lines).strip()
            ancestors = {k: v for k, v in ancestors.items() if k < section.level}
            if not text:
                continue
            # The H1 intro has no H2 path: it is cited by the document title.
            path = PATH_SEPARATOR.join(section.path_parts) or source.title
            kind = _classify(source.doc_key, section, text)
            parent = ancestors[max(ancestors)] if ancestors else None
            parts = (
                _pack(_blocks(section.lines), self.max_tokens, self.overlap)
                if count_tokens(text) > self.max_tokens
                else [text]
            )
            first = len(drafts)
            for number, part in enumerate(parts):
                drafts.append(
                    ChunkDraft(
                        section_path=path,
                        heading_level=section.level,
                        chunk_type=kind,
                        content=part,
                        embedded_text=f"{_prefix(source.title, path)}\n{part}",
                        token_count=count_tokens(part),
                        source_lines=f"{section.start}-{section.end}",
                        tags=source.tags,
                        parent_index=parent,
                        metadata={"part": number} if len(parts) > 1 else {},
                    )
                )
            if section.level >= 2:  # the H1 intro is not a parent of anything
                ancestors[section.level] = first
        return drafts


class FixedChunker:
    """Baseline for the ablation of spec 08: fixed windows, no structure."""

    name = "fixed_500"

    def __init__(self, size: int = FIXED_TOKENS, overlap: int = 50) -> None:
        self.size = size
        self.overlap = overlap

    def split(self, source: ChunkSource) -> list[ChunkDraft]:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.size,
            chunk_overlap=self.overlap,
            length_function=count_tokens,
        )
        return [
            ChunkDraft(
                section_path=source.title,
                heading_level=1,
                chunk_type="rule",
                content=text,
                embedded_text=text,
                token_count=count_tokens(text),
                source_lines="",
                tags=source.tags,
                metadata={"part": n},
            )
            for n, text in enumerate(splitter.split_text(source.body))
        ]


STRATEGIES: dict[str, Callable[[], Chunker]] = {
    "structural": StructuralChunker,
    "fixed_500": FixedChunker,
}


def get_chunker(strategy: str) -> Chunker:
    try:
        return STRATEGIES[strategy]()
    except KeyError:
        raise ValueError(
            f"unknown chunking strategy '{strategy}' (use {', '.join(STRATEGIES)})"
        ) from None

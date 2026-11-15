"""Pure metric functions of the RAG evals (spec 08). No I/O, no database, no LLM."""

import math
import re
from collections.abc import Iterable, Mapping, Sequence

_FIGURE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(%|por ciento|d[ií]as?|days?)", re.IGNORECASE
)
_SENTENCE = re.compile(r"(?<=[.!?:])\s+|\n+")
_SPACES = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    return _SPACES.sub(" ", text.replace("*", "").replace("|", " ")).strip().lower()


def recall_at_k(hits: Sequence[bool], expected: int) -> float:
    """Fraction of the `expected` sections found; `hits[i]` is whether expected[i]
    was covered by one of the top-k chunks."""
    if expected <= 0:
        raise ValueError("recall needs at least one expected section")
    return sum(1 for hit in hits if hit) / expected


def reciprocal_rank(covered_by_rank: Sequence[bool]) -> float:
    """1 / rank of the first chunk (1-based) that covers an expected section."""
    for rank, covered in enumerate(covered_by_rank, start=1):
        if covered:
            return 1.0 / rank
    return 0.0


def precision_at_k(covered_by_rank: Sequence[bool]) -> float:
    """Share of the retrieved chunks that carry an expected section."""
    return (
        sum(1 for c in covered_by_rank if c) / len(covered_by_rank)
        if covered_by_rank
        else 0.0
    )


def covers(
    chunk_path: str, chunk_text: str, section_path: str, section_text: str
) -> bool:
    """Does a retrieved chunk carry the expected section?

    Structural chunks match by `section_path`. Strategies that lose the structure
    (fixed windows) match by content: at least half of the section's sentences
    must appear verbatim in the chunk.
    """
    if chunk_path == section_path:
        return True
    sentences = [
        s
        for s in (normalize_text(p) for p in _SENTENCE.split(section_text))
        if len(s) > 12
    ]
    if not sentences:
        return False
    haystack = normalize_text(chunk_text)
    found = sum(1 for s in sentences if s in haystack)
    return found / len(sentences) >= 0.5


def abstention_correct(should_abstain: bool, needs_handoff: bool) -> bool:
    return should_abstain == needs_handoff


def figures(text: str) -> set[str]:
    """Day / percentage figures of a text, normalised ("30", "2.5")."""
    out: set[str] = set()
    for match in _FIGURE.finditer(text):
        number = match.group(1).replace(",", ".")
        out.add(number.rstrip("0").rstrip(".") if "." in number else number)
    return out


def unsupported_figures(answer: str, allowed: Iterable[str]) -> list[str]:
    allowed_set = {str(a) for a in allowed}
    return sorted(figures(answer) - allowed_set)


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile (`pct` in 0..100); 0.0 for no data."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def ratio(flags: Sequence[bool]) -> float | None:
    return sum(1 for f in flags if f) / len(flags) if flags else None


# --- thresholds ---------------------------------------------------------------

# metric -> (direction, label); "min": value must be >=, "max": value must be <.
DIRECTIONS: Mapping[str, str] = {
    "recall_at_5": "min",
    "faithfulness": "min",
    "citation_precision": "min",
    "correct_abstention": "min",
    "correct_figures": "min",
    "latency_p95_s": "max",
}


def check_threshold(metric: str, value: float | None, threshold: float) -> bool | None:
    """True/False against the threshold, None when the metric was not measured."""
    if value is None:
        return None
    if DIRECTIONS[metric] == "min":
        return value + 1e-9 >= threshold
    return value < threshold

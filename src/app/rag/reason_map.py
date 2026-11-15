"""Deterministic citations: reason codes -> policy sections, no search, no LLM.

The values of `PolicyConfig.reason_code_map` are written ``<label> > <section_path>``
(e.g. ``Elegibilidad > Ventana de 30 días``) where the label is a short name of the
document; chunks store only the ``<section_path>`` part (see `rag/chunking.py`).
"""

from collections.abc import Iterable

from app.domain.enums import RefundMethod
from app.domain.policy_config import ELIGIBILITY_DOC_KEY, PolicyConfig
from app.schemas.refund import Citation

PATH_SEPARATOR = " > "
METHODS_DOC_KEY = "refund-methods"

# Sections explaining where an eligible refund goes (validated against the corpus).
METHOD_CITATIONS: dict[RefundMethod, Citation] = {
    RefundMethod.ORIGINAL_PAYMENT: Citation(
        doc_key=METHODS_DOC_KEY,
        section_path="Métodos de reembolso > Reembolso al medio de pago original",
    ),
    RefundMethod.PLATFORM_CREDITS: Citation(
        doc_key=METHODS_DOC_KEY,
        section_path="Métodos de reembolso > Créditos de Learnly",
    ),
}
# The code whose section is the refund window (also cited for eligible purchases).
WINDOW_REASON_CODE = "OUTSIDE_REFUND_WINDOW"


def unqualify(path: str) -> str:
    """Drop the leading document label: the part chunks store as `section_path`."""
    _, sep, rest = path.partition(PATH_SEPARATOR)
    return rest if sep else path


def doc_label(config: PolicyConfig, doc_key: str, title: str) -> str:
    """Short document label used in citations (`Elegibilidad`, or the title)."""
    if doc_key == ELIGIBILITY_DOC_KEY:
        labels = {
            v.partition(PATH_SEPARATOR)[0] for v in config.reason_code_map.values()
        }
        if len(labels) == 1:
            return labels.pop()
    return title


def _dedupe(citations: Iterable[Citation]) -> list[Citation]:
    seen: set[tuple[str, str]] = set()
    unique: list[Citation] = []
    for citation in citations:
        key = (citation.doc_key, citation.section_path)
        if key not in seen:
            seen.add(key)
            unique.append(citation)
    return unique


def citations_for(config: PolicyConfig, keys: Iterable[str]) -> list[Citation]:
    """Citations for reason codes / warnings that have a mapped section."""
    return _dedupe(
        Citation(doc_key=ELIGIBILITY_DOC_KEY, section_path=path)
        for key in keys
        if (path := config.reason_code_map.get(str(key)))
    )


def eligible_citations(
    config: PolicyConfig, method: RefundMethod | None
) -> list[Citation]:
    """Window section plus the refund-method section of an eligible purchase."""
    citations = citations_for(config, [WINDOW_REASON_CODE])
    if method is not None:
        citations.append(METHOD_CITATIONS[method])
    return _dedupe(citations)

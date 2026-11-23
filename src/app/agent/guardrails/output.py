"""Output filters: nothing internal, foreign or invented reaches the customer (spec 12)."""

import re

from app.agent import prompts
from app.agent.prompts import SAFE_FALLBACK
from app.core.privacy import mask_email

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_SUPERVISOR_ID = re.compile(
    rf"(supervis\w*|aprobad\w* por|decided_by|approved by)[^\n]{{0,60}}?{_UUID}",
    re.IGNORECASE,
)
_INTERNAL = re.compile(
    r"nota[s]? interna|internal[ _]note|refund_internal_notes|decision_note_internal",
    re.IGNORECASE,
)
_TAGS = re.compile(
    r"</?(untrusted|customer_message[s]?|context|purchases|chunk)\b", re.IGNORECASE
)
POLICY_CLAIM = re.compile(
    r"\d+\s*(%|d[ií]as?|days?)|pol[ií]tica|plazo|reembolsable", re.IGNORECASE
)


def _prompt_markers() -> tuple[str, ...]:
    """Sentences of the agent's system prompts: reciting one means the prompt leaked."""
    markers: list[str] = []
    for name in (
        "CLASSIFIER_SYSTEM",
        "STATUS_SYSTEM",
        "COLLECT_SYSTEM",
        "EXPLAIN_SYSTEM",
    ):
        text = " ".join(str(getattr(prompts, name, "")).split())
        for sentence in re.split(r"(?<=[.:;])\s+", text):
            if len(sentence) >= 45:
                markers.append(sentence.lower())
    return tuple(markers)


_PROMPT_MARKERS = _prompt_markers()


def leaks_prompt(text: str) -> bool:
    flat = " ".join(text.lower().split())
    return bool(_TAGS.search(text)) or any(m in flat for m in _PROMPT_MARKERS)


def filter_output(text: str) -> tuple[str, list[str]]:
    flags: list[str] = []
    if _INTERNAL.search(text):
        return SAFE_FALLBACK, ["internal_note_leak_blocked"]
    if leaks_prompt(text):
        return SAFE_FALLBACK, ["prompt_leak_blocked"]
    redacted = _SUPERVISOR_ID.sub(
        lambda m: re.sub(_UUID, "[id interno]", m.group(0)), text
    )
    if redacted != text:
        flags.append("supervisor_id_redacted")
    masked = _EMAIL.sub(lambda m: mask_email(m.group(0)), redacted)
    if masked != redacted:
        flags.append("email_masked")
    return masked, flags

"""`guard_output`: nothing internal or foreign reaches the customer (challenge §2.5).

Deterministic filters only: internal-note vocabulary blocks the answer, emails are
masked, ids next to supervisor wording are redacted, and a policy claim without
citations is replaced by an abstention (spec 12 adds the injection detector).
"""

import re
from typing import Any

from app.agent.prompts import SAFE_FALLBACK, UNCITED_POLICY
from app.agent.state import Intent, SupportState
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
_POLICY_CLAIM = re.compile(
    r"\d+\s*(%|d[ií]as?|days?)|pol[ií]tica|plazo|reembolsable", re.IGNORECASE
)


def filter_output(text: str) -> tuple[str, list[str]]:
    flags: list[str] = []
    if _INTERNAL.search(text):
        return SAFE_FALLBACK, ["internal_note_leak_blocked"]
    redacted = _SUPERVISOR_ID.sub(
        lambda m: re.sub(_UUID, "[id interno]", m.group(0)), text
    )
    if redacted != text:
        flags.append("supervisor_id_redacted")
    masked = _EMAIL.sub(lambda m: mask_email(m.group(0)), redacted)
    if masked != redacted:
        flags.append("email_masked")
    return masked, flags


async def guard_output(state: SupportState) -> dict[str, Any]:
    text, flags = filter_output(state.get("draft", ""))
    flags = [*state.get("guardrail_flags", []), *flags]
    if (
        state.get("intent") == Intent.POLICY_QUESTION
        and not state.get("needs_handoff")
        and not state.get("citations")
        and _POLICY_CLAIM.search(text)
    ):
        text = UNCITED_POLICY
        flags.append("uncited_policy_claim")
    return {"draft": text, "guardrail_flags": flags}

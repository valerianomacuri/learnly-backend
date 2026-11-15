"""Degraded classifier by keywords, used when the LLM fails twice (challenge §6)."""

import re

from app.agent.state import Intent

_CANCEL = re.compile(
    r"cancel\w*\s+(mi|la|el|my|the)?\s*(solicitud|reembolso|refund|request)"
)
_HANDOFF = re.compile(
    r"\b(humano|persona real|asesor|hablar con (alguien|una persona)|human|"
    r"real person|agent)\b"
)
_STATUS = re.compile(
    r"(c[oó]mo va|estado de|en qu[eé] estado|seguimiento|status of|where is|how is)"
    r".*(solicitud|reembolso|refund|request)|mi solicitud|my (refund|request)"
)
_QUESTION = re.compile(
    r"\b(cu[aá]nt[oa]s?|c[oó]mo|qu[eé]|cu[aá]l|cu[aá]ndo|puedo|how|what|when|can i|"
    r"which|do i)\b|\?"
)
_REFUND_WORDS = re.compile(
    r"reembols|devoluci|pol[ií]tica|plazo|refund|policy|money back|window"
)
_REQUEST = re.compile(
    r"(quiero|solicitar|necesito|pido|i want|i need|request)\b.*"
    r"(reembols|devolver|refund|money back)"
)


def classify_by_keywords(text: str) -> Intent:
    lowered = text.lower()
    if _CANCEL.search(lowered):
        return Intent.CANCEL_REFUND
    if _HANDOFF.search(lowered):
        return Intent.HUMAN_HANDOFF
    if _STATUS.search(lowered):
        return Intent.REFUND_STATUS
    if _QUESTION.search(lowered) and _REFUND_WORDS.search(lowered):
        return Intent.POLICY_QUESTION
    if _REQUEST.search(lowered):
        return Intent.REFUND_REQUEST
    if _REFUND_WORDS.search(lowered):
        return Intent.POLICY_QUESTION
    return Intent.OUT_OF_SCOPE

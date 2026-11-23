"""PII masking applied before text reaches logs, the checkpointer or the LLM."""

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
# International (+34 612 345 678, (55) 1234-5678) or a plain run of 9-12 digits.
# ISO dates (2026-11-22) and amounts do not match.
_PHONE = re.compile(
    r"(?<![\w-])(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?)?\d{3,4}[ .-]\d{3,4}(?:[ .-]\d{2,4})?(?![\w-])"
    r"|(?<![\w-])\+?\d{9,12}(?![\w-])"
)
_TOKEN = re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}\b|\bsk-[A-Za-z0-9_-]{16,}\b")


def mask_pii(text: str) -> tuple[str, bool]:
    masked = _TOKEN.sub("[token]", text)
    masked = _EMAIL.sub("[email]", masked)
    masked = _CARD.sub("[número]", masked)
    masked = _PHONE.sub("[teléfono]", masked)
    return masked, masked != text

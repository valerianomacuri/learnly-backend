"""Personal-data helpers shared by every view that is not the data's owner."""

import logging
import re


def mask_email(email: str) -> str:
    """`leonardo@gmail.com` -> `l***@gmail.com`; keeps the domain, hides the rest.

    A value without a usable local part or domain is fully hidden rather than
    echoed back, so the helper can never leak a malformed address.
    """
    local, sep, domain = email.strip().rpartition("@")
    if not sep or not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SECRET = re.compile(
    r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}\b"  # JWT
    r"|\bsk-[A-Za-z0-9_-]{16,}\b"  # OpenAI key
    r"|(?i:bearer)\s+[\w.~+/=-]{12,}",
)


def redact(text: str) -> str:
    """Mask emails and credentials in free text (log lines)."""
    text = _SECRET.sub("[token]", text)
    return _EMAIL.sub(lambda m: mask_email(m.group(0)), text)


class RedactingFilter(logging.Filter):
    """Masks emails and tokens in every record that passes (spec 12).

    The message is formatted first, so arguments (`%s`, `%r`) and exception text
    are covered too; the formatted result replaces `msg` and clears `args`.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = redact(message)
        if cleaned != message or record.args:
            record.msg, record.args = cleaned, None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def install_log_privacy() -> None:
    """Attach the filter to every root handler (idempotent)."""
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())

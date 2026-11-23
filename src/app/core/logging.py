"""Structured JSON logs with request correlation (spec 13).

`trace_id` (the `X-Request-ID` of the request) and `user_id` (the id, never the
email) live in context variables, so every log line of a request carries them
without threading them through function signatures. The same `trace_id` is copied
into `refund_events.payload` and the outbox payload (see `current_trace_id`), and
the worker restores it when it processes the event.
"""

import contextvars
import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.core.privacy import RedactingFilter, redact

trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=""
)
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="")

# Attributes every LogRecord has; anything else was passed through `extra=`.
_STANDARD = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


def current_trace_id() -> str | None:
    """The trace id of the running request / event, or `None` outside one."""
    return trace_id_var.get() or None


class JsonFormatter(logging.Formatter):
    """One JSON object per line; message and exception text are redacted."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "trace_id": trace_id_var.get() or None,
            "user_id": user_id_var.get() or None,
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                entry[key] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            entry["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Root logger -> stderr in JSON, with the PII filter (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, "_learnly_json", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler._learnly_json = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)

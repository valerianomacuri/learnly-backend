"""JSON logs carry trace_id/user_id and stay redacted (spec 13)."""

import io
import json
import logging

from app.core.logging import JsonFormatter, trace_id_var, user_id_var
from app.core.privacy import RedactingFilter


def _logger() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger(f"test.json.{id(stream)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, stream


def test_line_is_json_with_ids_and_no_email() -> None:
    logger, stream = _logger()
    t1, t2 = trace_id_var.set("trace-1"), user_id_var.set("user-1")
    try:
        logger.info("hello ana@mail.com", extra={"refund_id": "r1"})
    finally:
        trace_id_var.reset(t1)
        user_id_var.reset(t2)
    entry = json.loads(stream.getvalue())
    assert entry["trace_id"] == "trace-1" and entry["user_id"] == "user-1"
    assert entry["refund_id"] == "r1"
    assert "ana@mail.com" not in stream.getvalue()


def test_no_context_gives_null_ids() -> None:
    logger, stream = _logger()
    logger.info("x")
    entry = json.loads(stream.getvalue())
    assert entry["trace_id"] is None and entry["user_id"] is None

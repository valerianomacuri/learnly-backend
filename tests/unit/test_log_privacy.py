"""The logging filter masks emails and credentials everywhere (spec 12)."""

import io
import logging

from app.core.privacy import RedactingFilter, redact

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJlMTIzNDU2"


def _logger() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger(f"test.privacy.{id(stream)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


def test_redact_masks_email_and_tokens() -> None:
    text = f"user ana@mail.com sent Bearer {JWT} and key sk-abcdefghijklmnopqrstuv"
    out = redact(text)
    assert "ana@mail.com" not in out and JWT not in out and "sk-abc" not in out
    assert "a***@mail.com" in out


def test_filter_covers_args_and_exceptions() -> None:
    logger, stream = _logger()
    logger.warning("failed for %s", "ana@mail.com")
    logger.warning("repr %r", ValueError("bad ana@mail.com"))
    try:
        raise RuntimeError(f"token {JWT}")
    except RuntimeError:
        logger.exception("boom")
    out = stream.getvalue()
    assert "ana@mail.com" not in out
    assert JWT not in out


def test_plain_messages_untouched() -> None:
    logger, stream = _logger()
    logger.info("payout %s done in %d ms", "abc", 12)
    assert "payout abc done in 12 ms" in stream.getvalue()

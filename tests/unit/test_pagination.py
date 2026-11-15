import uuid
from datetime import UTC, datetime

import pytest

from app.core.pagination import decode_cursor, encode_cursor


def test_cursor_round_trip() -> None:
    moment = datetime(2026, 11, 15, 12, 30, 45, 123456, tzinfo=UTC)
    row_id = uuid.uuid4()
    assert decode_cursor(encode_cursor(moment, row_id)) == (moment, row_id)


def test_cursor_is_url_safe() -> None:
    cursor = encode_cursor(datetime.now(UTC), uuid.uuid4())
    assert all(c.isalnum() or c in "-_" for c in cursor)


@pytest.mark.parametrize(
    "bad",
    ["", "not-a-cursor", "!!!!", "aGVsbG8", "MjAyNi0xMS0xNVQxMjowMDowMHxub3QtYS11dWlk"],
)
def test_malformed_cursor_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid cursor"):
        decode_cursor(bad)


def test_naive_timestamp_is_rejected() -> None:
    import base64

    raw = base64.urlsafe_b64encode(f"2026-11-15T12:00:00|{uuid.uuid4()}".encode())
    with pytest.raises(ValueError, match="invalid cursor"):
        decode_cursor(raw.decode().rstrip("="))

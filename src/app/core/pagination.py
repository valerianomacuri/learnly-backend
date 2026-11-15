"""Opaque keyset cursors: `(timestamp, id)` of the last row of a page."""

import base64
import binascii
import uuid
from datetime import datetime


def encode_cursor(moment: datetime, row_id: uuid.UUID) -> str:
    raw = f"{moment.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Inverse of `encode_cursor`; raises `ValueError` for anything malformed."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        moment, _, row_id = base64.urlsafe_b64decode(padded).decode().partition("|")
        parsed = datetime.fromisoformat(moment)
        if parsed.tzinfo is None:
            raise ValueError("cursor timestamp must be timezone-aware")
        return parsed, uuid.UUID(row_id)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid cursor") from exc

"""Supervisor live updates: Postgres `LISTEN/NOTIFY` rendered as Server-Sent Events.

`RefundEventService.append` NOTIFYs inside the writing transaction, so a message
only exists once the change is committed. Each stream holds one dedicated
connection (no Redis until spec 12).
"""

import json
from collections.abc import AsyncIterator

import psycopg

from app.services.refund_event_service import REFUND_CHANNEL

HEARTBEAT = ": heartbeat\n\n"


def to_psycopg_dsn(database_url: str) -> str:
    """`postgresql+psycopg://...` (SQLAlchemy) -> `postgresql://...` (libpq)."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def format_sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


async def refund_event_stream(
    database_url: str, *, heartbeat_seconds: float = 15.0
) -> AsyncIterator[str]:
    """SSE frames: `refund.created|updated` with `{id, status, version}`.

    Yields a comment heartbeat whenever `heartbeat_seconds` pass without events,
    which also lets the server notice a gone client.
    """
    async with await psycopg.AsyncConnection.connect(
        to_psycopg_dsn(database_url), autocommit=True
    ) as conn:
        await conn.execute(f"LISTEN {REFUND_CHANNEL}")
        yield HEARTBEAT  # flushes the headers so the client knows it is connected
        while True:
            got = False
            async for notice in conn.notifies(timeout=heartbeat_seconds):
                got = True
                data = json.loads(notice.payload)
                name = data.pop("event", "refund.updated")
                yield format_sse(name, json.dumps(data))
            if not got:
                yield HEARTBEAT

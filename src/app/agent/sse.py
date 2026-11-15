"""Server-sent events of the chat: wire format and graph-stream translation.

Events: `token`, `tool_call`, `tool_result`, `final`, `error` (spec 10 adds
`interrupt`). Each frame is `id:` (incremental per stream) + `event:` + `data:` JSON.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger("app.agent.sse")

EVENT_TYPES = ("token", "tool_call", "tool_result", "final", "error")
_CUSTOM_EVENTS = {"token", "tool_call", "tool_result"}


def format_event(event_id: int, event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"id: {event_id}\nevent: {event}\ndata: {payload}\n\n"


def _final(update: dict[str, Any]) -> dict[str, Any] | None:
    messages = update.get("messages") or []
    if not messages:
        return None
    message = messages[-1]
    return {
        "content": str(message.content),
        "citations": message.additional_kwargs.get("citations", []),
    }


async def translate(
    stream: AsyncIterator[tuple[str, Any]],
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """`astream(stream_mode=["updates", "custom"])` chunks -> `(event, data)`.

    Raw LLM tokens (`stream_mode="messages"`) are deliberately not forwarded: the
    answer is streamed by `respond` after `guard_output` has filtered it.
    """
    try:
        async for mode, payload in stream:
            if mode == "custom" and payload.get("type") in _CUSTOM_EVENTS:
                data = {k: v for k, v in payload.items() if k != "type"}
                yield payload["type"], data
            elif mode == "updates":
                update = payload.get("respond")
                final = _final(update) if isinstance(update, dict) else None
                if final is not None:
                    yield "final", final
    except Exception:
        logger.exception("agent run failed")
        # Never leak internals: the client gets a generic, stable error.
        yield (
            "error",
            {"code": "AGENT_ERROR", "message": "No pude completar la respuesta."},
        )


async def to_sse(
    events: AsyncIterator[tuple[str, dict[str, Any]]],
) -> AsyncIterator[str]:
    counter = 0
    async for event, data in events:
        counter += 1
        yield format_event(counter, event, data)

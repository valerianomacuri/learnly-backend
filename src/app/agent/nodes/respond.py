"""`respond`: turns the guarded draft into the assistant message and streams it."""

import re
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer

from app.agent.state import SupportState

_CHUNK = re.compile(r"\S+\s*")


async def respond(state: SupportState) -> dict[str, Any]:
    text = state.get("draft", "")
    citations = state.get("citations", [])
    emit = get_stream_writer()
    # Tokens come from the guarded text, never straight from a model, so nothing
    # unfiltered is ever streamed.
    for match in _CHUNK.finditer(text):
        emit({"type": "token", "text": match.group(0)})
    message = AIMessage(
        content=text,
        additional_kwargs={
            "citations": citations,
            "guardrail_flags": state.get("guardrail_flags", []),
        },
    )
    return {"messages": [message]}

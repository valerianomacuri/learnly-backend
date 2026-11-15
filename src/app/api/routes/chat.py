"""Chat with the agent (spec 09). Threads are owned; messages stream over SSE."""

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.agent.runtime import AgentRuntime
from app.agent.sse import to_sse
from app.api.deps import SessionDep, requires
from app.core.permissions import Perm
from app.core.security import Principal
from app.schemas.chat import ChatMessageRead, MessageCreate, ThreadRead
from app.services.chat_service import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])

ChatUserDep = Annotated[Principal, Depends(requires(Perm.CHAT_USE))]
# `scope="function"` releases the request session before the stream starts.
StreamUserDep = Annotated[Principal, Depends(requires(Perm.CHAT_USE), scope="function")]


def get_runtime(request: Request) -> AgentRuntime:
    runtime: AgentRuntime | None = getattr(request.app.state, "agent_runtime", None)
    if runtime is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The assistant is not available"
        )
    return runtime


RuntimeDep = Annotated[AgentRuntime, Depends(get_runtime)]


@router.post("/threads", response_model=ThreadRead, status_code=201)
async def create_thread(session: SessionDep, principal: ChatUserDep) -> ThreadRead:
    thread = await ChatService(session).create(principal)
    return ThreadRead.model_validate(thread)


@router.get("/threads", response_model=list[ThreadRead])
async def list_threads(
    session: SessionDep, principal: ChatUserDep
) -> Sequence[ThreadRead]:
    threads = await ChatService(session).list(principal)
    return [ThreadRead.model_validate(t) for t in threads]


@router.get("/threads/{thread_id}/messages", response_model=list[ChatMessageRead])
async def thread_messages(
    thread_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: ChatUserDep,
    runtime: RuntimeDep,
) -> list[ChatMessageRead]:
    """History rebuilt from the checkpointer (someone else's thread: 404)."""
    await ChatService(session).get(principal, thread_id)
    config = runtime.config(principal, thread_id, request.state.trace_id)
    return await runtime.history(config)


@router.post(
    "/threads/{thread_id}/messages",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def send_message(
    thread_id: uuid.UUID,
    body: MessageCreate,
    request: Request,
    principal: StreamUserDep,
    runtime: RuntimeDep,
) -> StreamingResponse:
    """SSE: `token`, `tool_call`, `tool_result`, `final` or `error`."""
    async with runtime.session_maker() as session:
        await ChatService(session).get(principal, thread_id)
    config = runtime.config(principal, thread_id, request.state.trace_id)
    return StreamingResponse(
        to_sse(runtime.stream(config, body.content)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )

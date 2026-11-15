"""Chat with the agent (spec 09). Threads are owned; messages stream over SSE."""

import uuid
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.agent.runtime import AgentRuntime
from app.agent.sse import to_sse
from app.api.deps import SessionDep, requires
from app.api.problems import PROBLEM_MEDIA_TYPE
from app.core.permissions import Perm
from app.core.security import Principal
from app.schemas.chat import ChatMessageRead, MessageCreate, ResumeRequest, ThreadRead
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


def _conflict(
    request: Request, code: str, payload: dict[str, Any] | None
) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", "")
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        media_type=PROBLEM_MEDIA_TYPE,
        content={
            "type": f"/problems/{code.lower().replace('_', '-')}",
            "code": code,
            "detail": "There is a confirmation waiting for an answer",
            "trace_id": trace_id,
            "interrupt": payload,
        },
    )


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
    history = await runtime.history(config)
    pending = await runtime.pending_interrupt(config)
    if pending is not None:
        history.append(ChatMessageRead(role="assistant", content="", interrupt=pending))
    return history


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
    pending = await runtime.pending_interrupt(config)
    if pending is not None:
        # The card does not disappear on its own: answer it through /resume.
        return _conflict(request, "INTERRUPT_PENDING", pending)  # type: ignore[return-value]
    return StreamingResponse(
        to_sse(runtime.stream(config, body.content)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/threads/{thread_id}/pending-interrupt",
    response_model=dict[str, Any],
    responses={204: {"description": "Nothing is waiting"}},
)
async def pending_interrupt(
    thread_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    principal: ChatUserDep,
    runtime: RuntimeDep,
) -> Response | dict[str, Any]:
    await ChatService(session).get(principal, thread_id)
    config = runtime.config(principal, thread_id, request.state.trace_id)
    pending = await runtime.pending_interrupt(config)
    if pending is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return pending


@router.post(
    "/threads/{thread_id}/resume",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def resume(
    thread_id: uuid.UUID,
    body: ResumeRequest,
    request: Request,
    principal: StreamUserDep,
    runtime: RuntimeDep,
) -> StreamingResponse:
    """SSE continuation of the graph. 409 if nothing waits or the id does not match."""
    async with runtime.session_maker() as session:
        await ChatService(session).get(principal, thread_id)
    config = runtime.config(principal, thread_id, request.state.trace_id)
    pending = await runtime.pending_interrupt(config)
    if pending is None or pending.get("interrupt_id") != body.interrupt_id:
        return _conflict(request, "INTERRUPT_NOT_PENDING", pending)  # type: ignore[return-value]
    return StreamingResponse(
        to_sse(runtime.resume(config, body.model_dump())),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )

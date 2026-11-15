"""Tool 10: `escalate_to_human` (low-risk write: marks the thread `NEEDS_HUMAN`).

`refund_events` is the only audit trail and needs a refund, so the escalation is
recorded in the structured log with `acting_for` and the run's `trace_id`.
"""

import logging
import uuid
from typing import Any, Literal

from pydantic import Field

from app.agent.tools.base import ToolArgs, ToolContext, ToolSpec
from app.domain.enums import ActorType, ThreadStatus
from app.repositories.chat_thread import ChatThreadRepository
from app.services.errors import NotFoundError

logger = logging.getLogger("app.agent.audit")


class EscalateArgs(ToolArgs):
    summary: str = Field(
        max_length=500, description="Resumen breve del caso para el equipo humano."
    )
    category: Literal["policy", "refund", "technical", "other"] = Field(
        default="other", description="Tipo de consulta."
    )


async def escalate_to_human(ctx: ToolContext, args: EscalateArgs) -> Any:
    principal = ctx.principal
    try:
        thread_id = uuid.UUID(principal.thread_id)
    except ValueError as exc:
        raise NotFoundError("Thread") from exc
    async with ctx.session_maker() as session:
        thread = await ChatThreadRepository(session).get_owned(
            thread_id, principal.user_id
        )
        if thread is None:
            raise NotFoundError("Thread")
        thread.status = ThreadStatus.NEEDS_HUMAN
        await session.commit()
    logger.info(
        "escalate_to_human",
        extra={
            "actor_type": ActorType.AGENT.value,
            "acting_for": str(principal.user_id),
            "thread_id": str(thread_id),
            "trace_id": principal.request_id,
            "category": args.category,
        },
    )
    return {"status": ThreadStatus.NEEDS_HUMAN.value}


SPEC = ToolSpec(
    name="escalate_to_human",
    description="Deriva la conversación a una persona del equipo de soporte.",
    args=EscalateArgs,
    handler=escalate_to_human,
    not_found_code="THREAD_NOT_FOUND",
)

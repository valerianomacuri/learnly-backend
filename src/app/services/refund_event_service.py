import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import current_trace_id
from app.domain.enums import ActorType
from app.models.refund import RefundEvent, RefundRequest
from app.repositories.refund_event import RefundEventRepository

REFUND_CHANNEL = "refund_events"  # Postgres NOTIFY channel (spec 06 SSE)


@dataclass(frozen=True, slots=True)
class EventActor:
    """Who caused an event. `id` is None for the system (workers, SLA checks)."""

    type: ActorType
    id: uuid.UUID | None = None


def _with_trace(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Stamp the request's `trace_id` (spec 13) so an event leads to its trace."""
    data = dict(payload or {})
    trace_id = current_trace_id()
    if trace_id and "trace_id" not in data:
        data["trace_id"] = trace_id
    return data


class RefundEventService:
    """The only way to write `refund_events` (specs 03-05 and 10 use it).

    `payload` is for the audit trail and the supervisor: customer schemas must
    never serialize it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = RefundEventRepository(session)

    async def append(
        self,
        refund_id: uuid.UUID,
        type: str,
        actor: EventActor,
        payload: dict[str, Any] | None = None,
        *,
        created_at: datetime | None = None,
    ) -> RefundEvent:
        """Add an event in the caller's transaction (flush only, no commit).

        `created_at` is only for back-dated data such as the seed; leave it unset
        to let the database stamp the event.
        """
        event = RefundEvent(
            refund_id=refund_id,
            type=type,
            actor_type=actor.type,
            actor_id=actor.id,
            payload=_with_trace(payload),
        )
        if created_at is not None:
            event.created_at = created_at
        added = await self.repo.add(event)
        await self._notify(refund_id, type)
        return added

    async def _notify(self, refund_id: uuid.UUID, event_type: str) -> None:
        """NOTIFY listeners of the supervisor stream; delivered only on commit.

        Callers append after mutating the refund, so the row carries the new
        status and version. A refund missing here (never, FK) is skipped.
        """
        refund = await self.session.get(RefundRequest, refund_id)
        if refund is None:
            return
        name = "refund.created" if event_type == "REFUND_CREATED" else "refund.updated"
        payload = json.dumps(
            {
                "event": name,
                "id": str(refund.id),
                "status": refund.status.value,
                "version": refund.version,
            }
        )
        await self.session.execute(select(func.pg_notify(REFUND_CHANNEL, payload)))

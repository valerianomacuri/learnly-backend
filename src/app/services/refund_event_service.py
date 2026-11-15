import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ActorType
from app.models.refund import RefundEvent
from app.repositories.refund_event import RefundEventRepository


@dataclass(frozen=True, slots=True)
class EventActor:
    """Who caused an event. `id` is None for the system (workers, SLA checks)."""

    type: ActorType
    id: uuid.UUID | None = None


class RefundEventService:
    """The only way to write `refund_events` (specs 03-05 and 10 use it).

    `payload` is for the audit trail and the supervisor: customer schemas must
    never serialize it.
    """

    def __init__(self, session: AsyncSession) -> None:
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
            payload=payload or {},
        )
        if created_at is not None:
            event.created_at = created_at
        return await self.repo.add(event)

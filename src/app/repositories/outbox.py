from datetime import datetime

from sqlalchemy import select

from app.domain.enums import OutboxStatus
from app.models.outbox import OutboxEvent
from app.repositories.base import BaseRepository

TOPIC_REFUND_PAYOUT = "refund.payout"


class OutboxRepository(BaseRepository[OutboxEvent]):
    model = OutboxEvent

    async def claim_next(self, topic: str, now: datetime) -> OutboxEvent | None:
        """Lock the oldest due PENDING event; rows other workers hold are skipped."""
        return await self.session.scalar(
            select(OutboxEvent)
            .where(
                OutboxEvent.topic == topic,
                OutboxEvent.status == OutboxStatus.PENDING,
                OutboxEvent.available_at <= now,
            )
            .order_by(OutboxEvent.available_at, OutboxEvent.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

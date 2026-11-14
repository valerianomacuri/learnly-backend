import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditDecision, AuditEvent
from app.repositories.audit_event import AuditEventRepository


class AuditService:
    """Append-only audit trail. Flushes only; the request transaction commits."""

    def __init__(self, session: AsyncSession) -> None:
        self.repo = AuditEventRepository(session)

    async def record(
        self,
        *,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID | None = None,
        decision: AuditDecision = AuditDecision.ALLOWED,
        reason: str | None = None,
    ) -> AuditEvent:
        return await self.repo.add(
            AuditEvent(
                actor=actor,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                decision=decision,
                reason=reason,
            )
        )

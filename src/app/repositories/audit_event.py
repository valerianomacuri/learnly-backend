from app.models.audit_event import AuditEvent
from app.repositories.base import BaseRepository


class AuditEventRepository(BaseRepository[AuditEvent]):
    model = AuditEvent

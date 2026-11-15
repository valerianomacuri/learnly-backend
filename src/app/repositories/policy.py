from sqlalchemy import select

from app.domain.enums import PolicyStatus
from app.domain.policy_config import PolicyConfig
from app.models.policy import PolicyDocument
from app.repositories.base import BaseRepository

# Parsed config keyed by the (doc_key, version, checksum) of the active documents:
# a new activation changes the key, so it invalidates the entry by itself.
_Fingerprint = tuple[tuple[str, str, str], ...]
_cache: tuple[_Fingerprint, PolicyConfig] | None = None


class PolicyRepository(BaseRepository[PolicyDocument]):
    model = PolicyDocument

    async def list_active(self) -> list[PolicyDocument]:
        result = await self.session.scalars(
            select(PolicyDocument)
            .where(PolicyDocument.status == PolicyStatus.ACTIVE)
            .order_by(PolicyDocument.doc_key)
        )
        return list(result.all())

    async def get_active_config(self) -> PolicyConfig:
        """The active policy parameters (`PolicyConfigError` if incomplete)."""
        global _cache
        documents = await self.list_active()
        fingerprint: _Fingerprint = tuple(
            (d.doc_key, d.version, d.checksum) for d in documents
        )
        if _cache is not None and _cache[0] == fingerprint:
            return _cache[1]
        config = PolicyConfig.from_documents(
            (d.doc_key, d.version, d.params) for d in documents
        )
        _cache = (fingerprint, config)
        return config

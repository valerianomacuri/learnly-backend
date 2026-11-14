import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base


class BaseRepository[ModelT: Base]:
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        return await self.session.get(self.model, entity_id)

    async def list(self, *, limit: int = 100, offset: int = 0) -> Sequence[ModelT]:
        result = await self.session.scalars(
            select(self.model)
            .order_by(self.model.created_at)  # type: ignore[attr-defined]
            .limit(limit)
            .offset(offset)
        )
        return result.all()

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        await self.session.delete(entity)
        await self.session.flush()

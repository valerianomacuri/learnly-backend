import uuid
from collections.abc import Sequence

from sqlalchemy import select

from app.models.chat import ChatThread
from app.repositories.base import BaseRepository


class ChatThreadRepository(BaseRepository[ChatThread]):
    model = ChatThread

    async def get_owned(
        self, thread_id: uuid.UUID, customer_id: uuid.UUID
    ) -> ChatThread | None:
        """Someone else's thread is indistinguishable from a missing one."""
        return await self.session.scalar(
            select(ChatThread).where(
                ChatThread.id == thread_id, ChatThread.customer_id == customer_id
            )
        )

    async def list_owned(
        self, customer_id: uuid.UUID, *, limit: int = 50
    ) -> Sequence[ChatThread]:
        result = await self.session.scalars(
            select(ChatThread)
            .where(ChatThread.customer_id == customer_id)
            .order_by(ChatThread.created_at.desc(), ChatThread.id)
            .limit(limit)
        )
        return result.all()

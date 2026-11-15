"""Chat threads: ownership. Messages live in the LangGraph checkpointer."""

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import Perm
from app.core.security import Principal
from app.models.chat import ChatThread
from app.repositories.chat_thread import ChatThreadRepository
from app.services.authz import require_permission
from app.services.errors import NotFoundError


class ChatService:
    def __init__(self, session: AsyncSession) -> None:
        self.threads = ChatThreadRepository(session)

    async def create(self, principal: Principal) -> ChatThread:
        require_permission(principal, Perm.CHAT_USE)
        return await self.threads.add(ChatThread(customer_id=principal.user_id))

    async def list(self, principal: Principal) -> Sequence[ChatThread]:
        require_permission(principal, Perm.CHAT_USE)
        return await self.threads.list_owned(principal.user_id)

    async def get(self, principal: Principal, thread_id: uuid.UUID) -> ChatThread:
        require_permission(principal, Perm.CHAT_USE)
        thread = await self.threads.get_owned(thread_id, principal.user_id)
        if thread is None:
            raise NotFoundError("Thread")
        return thread

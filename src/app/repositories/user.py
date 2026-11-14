import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.permissions import Perm
from app.core.security import Principal
from app.models.rbac import Role
from app.models.user import User
from app.repositories.base import BaseRepository
from app.repositories.scope import restrict


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        return await self.session.scalar(select(User).where(User.email == email))

    async def get_principal(self, user_id: uuid.UUID) -> Principal | None:
        """Load the user's roles and permissions (None if the user is gone)."""
        user = await self.session.scalar(
            select(User)
            .where(User.id == user_id)
            .options(selectinload(User.roles).selectinload(Role.permissions))
            .execution_options(populate_existing=True)
        )
        if user is None:
            return None
        return Principal(
            user_id=user.id,
            roles=frozenset(role.name for role in user.roles),
            permissions=frozenset(
                perm.code for role in user.roles for perm in role.permissions
            ),
        )

    async def get_scoped(self, user_id: uuid.UUID, principal: Principal) -> User | None:
        stmt = restrict(
            select(User).where(User.id == user_id), principal, Perm.USER_READ_ANY
        )
        return await self.session.scalar(stmt)

    async def list_filtered(
        self,
        *,
        email: str | None = None,
        role: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[User]:
        stmt = select(User)
        if email:
            stmt = stmt.where(User.email.icontains(email, autoescape=True))
        if role:
            stmt = stmt.where(User.roles.any(Role.name == role))
        result = await self.session.scalars(
            stmt.order_by(User.created_at, User.id).limit(limit).offset(offset)
        )
        return result.all()

    async def role_by_name(self, name: str) -> Role | None:
        return await self.session.scalar(select(Role).where(Role.name == name))

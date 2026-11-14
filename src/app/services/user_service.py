import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.passwords import hash_password
from app.core.permissions import Perm, RoleName
from app.core.security import Principal
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserUpdate
from app.services.authz import require_any_permission, require_permission
from app.services.errors import ConflictError, NotFoundError


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = UserRepository(session)

    async def create(self, principal: Principal, data: UserCreate) -> User:
        """Create a user with the `customer` role (other roles are seeded)."""
        require_permission(principal, Perm.USER_CREATE)
        if await self.repo.get_by_email(data.email):
            raise ConflictError("User with this email already exists")
        role = await self.repo.role_by_name(RoleName.CUSTOMER)
        if role is None:
            raise NotFoundError("Role")
        user = User(
            email=data.email,
            name=data.name,
            password_hash=hash_password(data.password.get_secret_value()),
        )
        user.roles.append(role)
        return await self.repo.add(user)

    async def get(self, principal: Principal, user_id: uuid.UUID) -> User:
        require_any_permission(principal, Perm.USER_READ_ANY, Perm.USER_READ_OWN)
        user = await self.repo.get_scoped(user_id, principal)
        if user is None:
            raise NotFoundError("User")
        return user

    async def list(
        self,
        principal: Principal,
        *,
        email: str | None = None,
        role: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[User]:
        require_permission(principal, Perm.USER_READ_ANY)
        return await self.repo.list_filtered(
            email=email, role=role, limit=limit, offset=offset
        )

    async def update(
        self, principal: Principal, user_id: uuid.UUID, data: UserUpdate
    ) -> User:
        require_any_permission(principal, Perm.USER_UPDATE_ANY, Perm.USER_UPDATE_OWN)
        user = await self.get(principal, user_id)
        if not principal.has(Perm.USER_UPDATE_ANY) and user.id != principal.user_id:
            raise NotFoundError("User")  # pragma: no cover - scope already hides it
        changes = data.model_dump(exclude_unset=True)
        password = changes.pop("password", None)
        if password is not None:
            user.password_hash = hash_password(password.get_secret_value())
        new_email = changes.get("email")
        if new_email and new_email != user.email:
            if await self.repo.get_by_email(new_email):
                raise ConflictError("User with this email already exists")
        for field, value in changes.items():
            setattr(user, field, value)
        await self.repo.session.flush()
        await self.repo.session.refresh(user)
        return user

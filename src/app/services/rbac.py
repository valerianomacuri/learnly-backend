from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.permissions import ROLE_PERMISSIONS, Perm, RoleName
from app.models.rbac import Permission, Role


async def ensure_rbac(session: AsyncSession) -> dict[RoleName, Role]:
    """Make the `permissions`, `roles` and `role_permissions` tables match the catalog.

    Idempotent. Used by the seed and the tests; production roles are managed in
    the database after that. Flushes only; the caller commits.
    """
    permissions = {
        perm.code: perm for perm in await session.scalars(select(Permission))
    }
    for perm in Perm:
        if perm.value not in permissions:
            permissions[perm.value] = Permission(
                code=perm.value, description=perm.value
            )
            session.add(permissions[perm.value])

    roles = {
        role.name: role
        for role in await session.scalars(
            select(Role).options(selectinload(Role.permissions))
        )
    }
    for name, granted in ROLE_PERMISSIONS.items():
        role = roles.get(name.value)
        if role is None:
            role = roles[name.value] = Role(name=name.value, permissions=[])
            session.add(role)
        role.permissions = sorted(
            (permissions[perm.value] for perm in granted), key=lambda p: p.code
        )
    await session.flush()
    return {RoleName(name): role for name, role in roles.items()}

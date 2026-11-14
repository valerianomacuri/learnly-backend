import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import PrincipalDep, UserServiceDep, requires
from app.core.permissions import Perm
from app.core.security import Principal
from app.models.user import User
from app.schemas.user import UserCreate, UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserCreate,
    service: UserServiceDep,
    principal: Annotated[Principal, Depends(requires(Perm.USER_CREATE))],
) -> User:
    return await service.create(principal, data)


@router.get("", response_model=list[UserRead])
async def list_users(
    service: UserServiceDep,
    principal: Annotated[Principal, Depends(requires(Perm.USER_READ_ANY))],
    email: Annotated[str | None, Query(max_length=320)] = None,
    role: Annotated[str | None, Query(max_length=50)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Sequence[User]:
    """Supervisor listing; `email` is a case-insensitive substring match."""
    return await service.list(
        principal, email=email, role=role, limit=limit, offset=offset
    )


@router.get("/{user_id}", response_model=UserRead)
async def get_user(
    user_id: uuid.UUID, service: UserServiceDep, principal: PrincipalDep
) -> User:
    return await service.get(principal, user_id)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    service: UserServiceDep,
    principal: PrincipalDep,
) -> User:
    return await service.update(principal, user_id, data)

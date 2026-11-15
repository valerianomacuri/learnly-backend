from fastapi import APIRouter

from app.api.deps import MeServiceDep, PrincipalDep
from app.schemas.me import MeRead

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeRead)
async def read_me(service: MeServiceDep, principal: PrincipalDep) -> MeRead:
    """Profile, roles and credit balance of the authenticated caller."""
    return await service.get(principal)

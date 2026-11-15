import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import PurchaseServiceDep, requires
from app.core.permissions import Perm
from app.core.security import Principal
from app.domain.enums import PurchaseStatus
from app.schemas.purchase import PurchaseDetail, PurchasePage

router = APIRouter(prefix="/purchases", tags=["purchases"])

OwnerDep = Annotated[Principal, Depends(requires(Perm.PURCHASE_READ_OWN))]


@router.get("", response_model=PurchasePage)
async def list_purchases(
    service: PurchaseServiceDep,
    principal: OwnerDep,
    status: PurchaseStatus | None = None,
    within_window: Annotated[
        bool | None,
        Query(description="Inside (true) or outside (false) the refund window"),
    ] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PurchasePage:
    """Own purchases, newest first, paginated by cursor."""
    return await service.list(
        principal,
        status=status,
        within_window=within_window,
        cursor=cursor,
        limit=limit,
    )


@router.get("/{purchase_id}", response_model=PurchaseDetail)
async def get_purchase(
    purchase_id: uuid.UUID, service: PurchaseServiceDep, principal: OwnerDep
) -> PurchaseDetail:
    """Purchase with progress and refund deadline; 404 if missing or not yours."""
    return await service.get(principal, purchase_id)

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app.api.deps import RefundServiceDep, requires
from app.api.problems import problem_response
from app.core.permissions import Perm
from app.core.security import Principal
from app.domain.enums import Channel, RefundStatus
from app.schemas.refund import (
    PreviewRequest,
    PreviewResult,
    RefundCreate,
    RefundEventRead,
    RefundPage,
    RefundRead,
)
from app.services.errors import InvalidInputError, RefundNotEligible

router = APIRouter(prefix="/refunds", tags=["refunds"])

SimulatorDep = Annotated[Principal, Depends(requires(Perm.REFUND_SIMULATE))]
CreatorDep = Annotated[Principal, Depends(requires(Perm.REFUND_CREATE))]
ReaderDep = Annotated[Principal, Depends(requires(Perm.REFUND_READ_OWN))]
CancellerDep = Annotated[Principal, Depends(requires(Perm.REFUND_CANCEL_OWN))]


@router.post("/preview", response_model=PreviewResult)
async def preview_refund(
    body: PreviewRequest, service: RefundServiceDep, principal: SimulatorDep
) -> PreviewResult:
    """Evaluate eligibility; only an eligible preview carries a confirmation token."""
    return await service.preview(principal, body)


@router.post("", response_model=RefundRead, status_code=status.HTTP_201_CREATED)
async def create_refund(
    request: Request,
    body: RefundCreate,
    service: RefundServiceDep,
    principal: CreatorDep,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ],
) -> RefundRead | Response:
    """Create a refund. The same key and body replay the original response."""
    try:
        return await service.create_request(
            principal,
            body,
            idempotency_key=idempotency_key,
            confirmation_proof=body.confirmation_token,
            channel=Channel.MANUAL_FORM,
        )
    except RefundNotEligible as exc:
        # Returned, not raised: raising would roll back the `eligibility_checks`
        # row that records the rejected attempt (D6). Nothing else was written.
        return problem_response(request, exc)


@router.get("", response_model=RefundPage)
async def list_refunds(
    service: RefundServiceDep,
    principal: ReaderDep,
    status: RefundStatus | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RefundPage:
    return await service.list(principal, status=status, cursor=cursor, limit=limit)


@router.get("/{refund_id}", response_model=RefundRead)
async def get_refund(
    refund_id: uuid.UUID, service: RefundServiceDep, principal: ReaderDep
) -> RefundRead:
    return await service.get(principal, refund_id)


@router.get("/{refund_id}/events", response_model=list[RefundEventRead])
async def refund_events(
    refund_id: uuid.UUID, service: RefundServiceDep, principal: ReaderDep
) -> Sequence[RefundEventRead]:
    return await service.timeline(principal, refund_id)


def _parse_version(if_match: str) -> int:
    raw = if_match.strip().removeprefix("W/").strip('"')
    if not raw.isdigit():
        raise InvalidInputError("If-Match must be the refund version")
    return int(raw)


@router.post("/{refund_id}/cancel", response_model=RefundRead)
async def cancel_refund(
    refund_id: uuid.UUID,
    service: RefundServiceDep,
    principal: CancellerDep,
    if_match: Annotated[str, Header(alias="If-Match", max_length=50)],
) -> RefundRead:
    return await service.cancel(principal, refund_id, if_match=_parse_version(if_match))

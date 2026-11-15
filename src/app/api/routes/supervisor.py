import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status

from app.api.deps import SupervisorRefundServiceDep, requires
from app.core.permissions import Perm
from app.core.security import Principal
from app.domain.enums import Channel, QueueSort, ReasonCategory, RefundStatus, SortOrder
from app.schemas.supervisor import (
    DecisionRequest,
    NoteCreate,
    NoteRead,
    SupervisorEventRead,
    SupervisorRefundDetail,
    SupervisorRefundPage,
    SupervisorRefundRead,
    SupervisorStats,
)
from app.services.errors import IfMatchRequired, InvalidIfMatch
from app.services.supervisor_refund_service import QueueFilters

router = APIRouter(prefix="/supervisor", tags=["supervisor"])

ReaderDep = Annotated[Principal, Depends(requires(Perm.REFUND_READ_ANY))]
DeciderDep = Annotated[Principal, Depends(requires(Perm.REFUND_DECIDE))]
PayoutRetrierDep = Annotated[Principal, Depends(requires(Perm.REFUND_RETRY_PAYOUT))]
NoteWriterDep = Annotated[Principal, Depends(requires(Perm.NOTE_WRITE))]
NoteReaderDep = Annotated[Principal, Depends(requires(Perm.NOTE_READ))]

IfMatchHeader = Annotated[str | None, Header(alias="If-Match", max_length=50)]


def parse_if_match(if_match: str | None) -> int:
    """The refund version from `If-Match` (428 if absent, 400 if malformed)."""
    if if_match is None or not if_match.strip():
        raise IfMatchRequired
    raw = if_match.strip().removeprefix("W/").strip('"')
    if not raw.isdigit():
        raise InvalidIfMatch
    return int(raw)


@router.get("/refunds", response_model=SupervisorRefundPage)
async def list_queue(
    service: SupervisorRefundServiceDep,
    principal: ReaderDep,
    status: RefundStatus | None = None,
    sla_breached: bool | None = None,
    high_risk: bool | None = None,
    channel: Channel | None = None,
    reason_category: ReasonCategory | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    customer: Annotated[str | None, Query(max_length=100)] = None,
    sort: QueueSort = QueueSort.CREATED_AT,
    order: SortOrder | None = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SupervisorRefundPage:
    return await service.list_queue(
        principal,
        QueueFilters(
            status=status,
            sla_breached=sla_breached,
            high_risk=high_risk,
            channel=channel,
            reason_category=reason_category,
            created_from=created_from,
            created_to=created_to,
            customer=customer,
            sort=sort,
            order=order,
            cursor=cursor,
            limit=limit,
        ),
    )


@router.get("/stats", response_model=SupervisorStats)
async def stats(
    service: SupervisorRefundServiceDep, principal: ReaderDep
) -> SupervisorStats:
    return await service.stats(principal)


@router.get("/refunds/{refund_id}", response_model=SupervisorRefundDetail)
async def get_refund(
    refund_id: uuid.UUID, service: SupervisorRefundServiceDep, principal: ReaderDep
) -> SupervisorRefundDetail:
    return await service.get_detail(principal, refund_id)


@router.post("/refunds/{refund_id}/approve", response_model=SupervisorRefundRead)
async def approve_refund(
    refund_id: uuid.UUID,
    service: SupervisorRefundServiceDep,
    principal: DeciderDep,
    if_match: IfMatchHeader = None,
    body: DecisionRequest | None = None,
) -> SupervisorRefundRead:
    version = parse_if_match(if_match)
    return await service.approve(
        principal,
        refund_id,
        if_match=version,
        note=body.note if body is not None else None,
    )


@router.post("/refunds/{refund_id}/reject", response_model=SupervisorRefundRead)
async def reject_refund(
    refund_id: uuid.UUID,
    service: SupervisorRefundServiceDep,
    principal: DeciderDep,
    if_match: IfMatchHeader = None,
    body: DecisionRequest | None = None,
) -> SupervisorRefundRead:
    version = parse_if_match(if_match)
    return await service.reject(
        principal,
        refund_id,
        if_match=version,
        note=body.note if body is not None else None,
    )


@router.post(
    "/refunds/{refund_id}/retry-payout",
    response_model=SupervisorRefundRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_payout(
    refund_id: uuid.UUID,
    service: SupervisorRefundServiceDep,
    principal: PayoutRetrierDep,
) -> SupervisorRefundRead:
    return await service.retry_payout(principal, refund_id)


@router.get("/refunds/{refund_id}/notes", response_model=list[NoteRead])
async def list_notes(
    refund_id: uuid.UUID, service: SupervisorRefundServiceDep, principal: NoteReaderDep
) -> Sequence[NoteRead]:
    return await service.list_notes(principal, refund_id)


@router.post(
    "/refunds/{refund_id}/notes",
    response_model=NoteRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_note(
    refund_id: uuid.UUID,
    body: NoteCreate,
    service: SupervisorRefundServiceDep,
    principal: NoteWriterDep,
) -> NoteRead:
    return await service.add_note(principal, refund_id, body.body)


@router.get("/refunds/{refund_id}/events", response_model=list[SupervisorEventRead])
async def refund_events(
    refund_id: uuid.UUID, service: SupervisorRefundServiceDep, principal: ReaderDep
) -> Sequence[SupervisorEventRead]:
    return await service.timeline(principal, refund_id)

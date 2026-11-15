"""Tools 7 and 8: own refund requests (read). Never internal notes or payloads."""

import uuid
from typing import Any

from pydantic import Field

from app.agent.tools.base import ToolArgs, ToolContext, ToolSpec
from app.domain.enums import RefundStatus
from app.services.refund_service import RefundService

MAX_REFUNDS = 20


class ListRefundsArgs(ToolArgs):
    status: RefundStatus | None = Field(
        default=None, description="Filtra por estado de la solicitud."
    )


class RefundIdArgs(ToolArgs):
    refund_id: uuid.UUID = Field(description="Id de la solicitud de reembolso (UUID).")


def _summary(refund: Any) -> dict[str, Any]:
    payout = refund.payout
    return {
        "id": refund.id,
        "purchase_id": refund.purchase_id,
        "status": refund.status,
        "reason_category": refund.reason_category,
        "refund_method": refund.refund_method,
        "amount": refund.amount,
        "currency": refund.currency,
        "created_at": refund.created_at,
        "decided_at": refund.decided_at,
        "decision_note": refund.decision_note,
        "payout_status": payout.status if payout is not None else None,
    }


async def list_my_refunds(ctx: ToolContext, args: ListRefundsArgs) -> Any:
    principal = ctx.principal.to_service_principal()
    async with ctx.session_maker() as session:
        page = await RefundService(session).list(
            principal, status=args.status, limit=MAX_REFUNDS
        )
    return {"refunds": [_summary(r) for r in page.items]}


async def get_refund_status(ctx: ToolContext, args: RefundIdArgs) -> Any:
    principal = ctx.principal.to_service_principal()
    async with ctx.session_maker() as session:
        service = RefundService(session)
        refund = await service.get(principal, args.refund_id)
        events = await service.timeline(principal, args.refund_id)
    return {
        **_summary(refund),
        "timeline": [
            {"type": e.type, "actor": e.actor_type, "at": e.created_at} for e in events
        ],
    }


LIST_SPEC = ToolSpec(
    name="list_my_refunds",
    description="Lista las solicitudes de reembolso del cliente actual.",
    args=ListRefundsArgs,
    handler=list_my_refunds,
)
STATUS_SPEC = ToolSpec(
    name="get_refund_status",
    description="Estado y línea de tiempo pública de un reembolso propio.",
    args=RefundIdArgs,
    handler=get_refund_status,
    not_found_code="REFUND_NOT_FOUND",
)

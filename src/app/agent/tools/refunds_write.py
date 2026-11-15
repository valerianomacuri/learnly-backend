"""Tools 6 and 9 (write). Invoked only by deterministic nodes behind HITL.

They are never bound to a model (`LLM_BOUND_TOOLS`): their arguments come from the
graph state (what the customer confirmed), not from anything the LLM produced.
"""

import uuid
from typing import Any

from pydantic import Field

from app.agent.tools.base import ToolArgs, ToolContext, ToolSpec
from app.domain.enums import ActorType, Channel, ReasonCategory
from app.schemas.refund import RefundCreate
from app.services.errors import RefundNotEligible
from app.services.refund_service import RefundService


class CreateRefundArgs(ToolArgs):
    purchase_id: uuid.UUID = Field(description="Id de la compra (UUID).")
    reason_category: ReasonCategory = Field(description="Categoría del motivo.")
    reason_text: str = Field(max_length=2000, description="Motivo confirmado.")
    confirmation_proof: str | None = Field(
        default=None, description="Token firmado de la confirmación."
    )
    idempotency_key: str = Field(min_length=8, max_length=128)


class CancelRefundArgs(ToolArgs):
    refund_id: uuid.UUID = Field(description="Id de la solicitud (UUID).")
    if_match: int = Field(ge=1, description="Versión de la solicitud que se leyó.")


async def create_refund_request(ctx: ToolContext, args: CreateRefundArgs) -> Any:
    if not args.confirmation_proof:
        return {"created": False, "error": "CONFIRMATION_REQUIRED", "reason_codes": []}
    principal = ctx.principal.to_service_principal()
    thread_id = uuid.UUID(ctx.principal.thread_id) if ctx.principal.thread_id else None
    async with ctx.session_maker() as session:
        try:
            refund = await RefundService(session).create_request(
                principal,
                RefundCreate(
                    purchase_id=args.purchase_id,
                    reason_category=args.reason_category,
                    reason_text=args.reason_text,
                    confirmation_token=args.confirmation_proof,
                ),
                idempotency_key=args.idempotency_key,
                confirmation_proof=args.confirmation_proof,
                channel=Channel.CHAT,
                chat_thread_id=thread_id,
            )
        except RefundNotEligible as exc:
            # The failed attempt is persisted (eligibility_checks), as in POST /refunds.
            await session.commit()
            return {
                "created": False,
                "error": exc.code,
                "reason_codes": [str(c) for c in exc.reason_codes],
            }
        await session.commit()
    return {
        "created": True,
        "refund_id": refund.id,
        "status": refund.status,
        "amount": refund.amount,
        "currency": refund.currency,
        "version": refund.version,
    }


async def cancel_refund_request(ctx: ToolContext, args: CancelRefundArgs) -> Any:
    principal = ctx.principal.to_service_principal()
    async with ctx.session_maker() as session:
        refund = await RefundService(session).cancel(
            principal, args.refund_id, if_match=args.if_match, actor=ActorType.AGENT
        )
        await session.commit()
    return {"refund_id": refund.id, "status": refund.status, "version": refund.version}


CREATE_SPEC = ToolSpec(
    name="create_refund_request",
    description="Crea la solicitud de reembolso ya confirmada por el cliente.",
    args=CreateRefundArgs,
    handler=create_refund_request,
    not_found_code="PURCHASE_NOT_FOUND",
)
CANCEL_SPEC = ToolSpec(
    name="cancel_refund_request",
    description="Cancela una solicitud propia en PENDING_APPROVAL ya confirmada.",
    args=CancelRefundArgs,
    handler=cancel_refund_request,
    not_found_code="REFUND_NOT_FOUND",
)

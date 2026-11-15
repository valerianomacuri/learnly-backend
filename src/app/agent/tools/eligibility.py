"""Tools 4 and 5 (read). Called by deterministic nodes only, never bound to a model."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import Field

from app.agent.tools.base import ToolArgs, ToolContext, ToolSpec
from app.core.permissions import Perm
from app.domain import confirmation
from app.domain.enums import CheckSource, ReasonCategory
from app.domain.reason import validate_reason
from app.schemas.eligibility import EligibilityRead
from app.schemas.refund import PreviewRequest
from app.services.authz import require_permission
from app.services.eligibility_service import EligibilityService
from app.services.purchase_service import PurchaseService
from app.services.refund_service import RefundService


class CheckEligibilityArgs(ToolArgs):
    purchase_id: uuid.UUID = Field(description="Id de la compra (UUID).")


class PrepareConfirmationArgs(ToolArgs):
    purchase_id: uuid.UUID = Field(description="Id de la compra (UUID).")
    reason_category: ReasonCategory = Field(description="Categoría del motivo.")
    reason_text: str = Field(
        max_length=2000, description="Motivo en palabras del cliente."
    )


async def check_refund_eligibility(ctx: ToolContext, args: CheckEligibilityArgs) -> Any:
    principal = ctx.principal.to_service_principal()
    require_permission(principal, Perm.REFUND_SIMULATE)
    async with ctx.session_maker() as session:
        result, _ = await EligibilityService(session).evaluate(
            principal, args.purchase_id, CheckSource.AGENT
        )
        await session.commit()  # the check is recorded (eligibility_checks)
    return EligibilityRead.from_result(result)


@dataclass(frozen=True)
class Prepared:
    """What `prepare_confirmation` needs: the model-safe preview and the secrets."""

    preview: dict[str, Any]
    token: str | None
    hash: str | None
    purchase_title: str
    window_deadline: datetime | None


async def prepare(ctx: ToolContext, args: PrepareConfirmationArgs) -> Prepared:
    principal = ctx.principal.to_service_principal()
    async with ctx.session_maker() as session:
        preview = await RefundService(session).preview(
            principal,
            PreviewRequest(
                purchase_id=args.purchase_id,
                reason_category=args.reason_category,
                reason_text=args.reason_text,
            ),
        )
        purchase = await PurchaseService(session).get(principal, args.purchase_id)
        await session.commit()
    digest: str | None = None
    if preview.eligible and preview.amount is not None and preview.refund_method:
        category, text = validate_reason(args.reason_category, args.reason_text)
        digest = confirmation.payload_hash(
            purchase_id=args.purchase_id,
            reason_category=category,
            reason_text=text,
            amount=preview.amount,
            refund_method=preview.refund_method,
        )
    return Prepared(
        # The signed token never reaches the model's context.
        preview=preview.model_dump(mode="json", exclude={"confirmation_token"}),
        token=preview.confirmation_token,
        hash=digest,
        purchase_title=purchase.course.title,
        window_deadline=purchase.refund_deadline,
    )


async def prepare_refund_confirmation(
    ctx: ToolContext, args: PrepareConfirmationArgs
) -> Any:
    return (await prepare(ctx, args)).preview


ELIGIBILITY_SPEC = ToolSpec(
    name="check_refund_eligibility",
    description="Evalúa si una compra propia es elegible para reembolso.",
    args=CheckEligibilityArgs,
    handler=check_refund_eligibility,
    not_found_code="PURCHASE_NOT_FOUND",
)
PREPARE_SPEC = ToolSpec(
    name="prepare_refund_confirmation",
    description="Valida el motivo y prepara la confirmación (no crea nada).",
    args=PrepareConfirmationArgs,
    handler=prepare_refund_confirmation,
    not_found_code="PURCHASE_NOT_FOUND",
)

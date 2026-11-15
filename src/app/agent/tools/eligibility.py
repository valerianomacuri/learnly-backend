"""Tools 4 and 5 (read). Defined here, bound to no model until spec 10 uses them."""

import uuid
from typing import Any

from pydantic import Field

from app.agent.tools.base import ToolArgs, ToolContext, ToolSpec
from app.core.permissions import Perm
from app.domain.enums import CheckSource, ReasonCategory
from app.schemas.eligibility import EligibilityRead
from app.schemas.refund import PreviewRequest
from app.services.authz import require_permission
from app.services.eligibility_service import EligibilityService
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


async def prepare_refund_confirmation(
    ctx: ToolContext, args: PrepareConfirmationArgs
) -> Any:
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
        await session.commit()
    # The signed token never reaches the model's context.
    return preview.model_dump(mode="json", exclude={"confirmation_token"})


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

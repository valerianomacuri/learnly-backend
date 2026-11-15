"""Tools 2 and 3: own purchases (read). Foreign purchases look like missing ones."""

import uuid
from typing import Any

from pydantic import Field

from app.agent.tools.base import ToolArgs, ToolContext, ToolSpec
from app.domain.enums import PurchaseStatus
from app.services.purchase_service import PurchaseService

MAX_PURCHASES = 20


class ListPurchasesArgs(ToolArgs):
    status: PurchaseStatus | None = Field(
        default=None, description="Filtra por estado de la compra."
    )
    within_refund_window: bool | None = Field(
        default=None, description="true: solo compras dentro del plazo de reembolso."
    )
    course_query: str | None = Field(
        default=None,
        max_length=100,
        description="Texto a buscar en el título del curso.",
    )


class PurchaseIdArgs(ToolArgs):
    purchase_id: uuid.UUID = Field(description="Id de la compra (UUID).")


async def list_my_purchases(ctx: ToolContext, args: ListPurchasesArgs) -> Any:
    principal = ctx.principal.to_service_principal()
    async with ctx.session_maker() as session:
        page = await PurchaseService(session).list(
            principal,
            status=args.status,
            within_window=args.within_refund_window,
            limit=MAX_PURCHASES,
        )
    needle = (args.course_query or "").strip().lower()
    items = [p for p in page.items if needle in p.course.title.lower()]
    # Minimal projection: nothing the model does not need to talk about a purchase.
    return {
        "purchases": [
            {
                "id": p.id,
                "course": p.course.title,
                "purchased_at": p.purchased_at,
                "amount": p.amount,
                "currency": p.currency,
                "status": p.status,
                "within_refund_window": p.within_refund_window,
                "refund_deadline": p.refund_deadline,
            }
            for p in items[:MAX_PURCHASES]
        ]
    }


async def get_purchase_details(ctx: ToolContext, args: PurchaseIdArgs) -> Any:
    principal = ctx.principal.to_service_principal()
    async with ctx.session_maker() as session:
        return await PurchaseService(session).get(principal, args.purchase_id)


LIST_SPEC = ToolSpec(
    name="list_my_purchases",
    description=(
        "Lista las compras del cliente actual (máx. 20, más recientes primero). "
        "No necesita identificar al cliente."
    ),
    args=ListPurchasesArgs,
    handler=list_my_purchases,
)
DETAILS_SPEC = ToolSpec(
    name="get_purchase_details",
    description="Detalle de una compra del cliente actual, con su progreso.",
    args=PurchaseIdArgs,
    handler=get_purchase_details,
    not_found_code="PURCHASE_NOT_FOUND",
)

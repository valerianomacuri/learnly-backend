"""Tools 6 and 9: they refuse unconfirmed or mismatching requests (spec 10)."""

import uuid

from agent_helpers import new_thread
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_tools_behaviour import call, ctx_for

from app.agent.tools.eligibility import PrepareConfirmationArgs, prepare
from app.domain.enums import ReasonCategory, RefundStatus
from app.models.refund import RefundRequest
from conftest import MakePurchase, MakeRefund, MakeUser

Maker = async_sessionmaker[AsyncSession]
REASON = "El contenido del curso no corresponde con la descripción publicada."
OTHER_REASON = "Los videos del módulo tres no cargan desde hace varios días."


async def refund_rows(maker: Maker) -> list[RefundRequest]:
    async with maker() as session:
        return list((await session.scalars(select(RefundRequest))).all())


async def test_create_without_proof_is_refused(
    session_maker: Maker, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    purchase = await make_purchase(ann)

    result = await call(
        ctx_for(session_maker, ann.id, str(uuid.uuid4())),
        "create_refund_request",
        purchase_id=purchase.id,
        reason_category="NOT_AS_DESCRIBED",
        reason_text=REASON,
        idempotency_key="k" * 16,
    )

    assert result.ok and result.data["created"] is False
    assert await refund_rows(session_maker) == []


async def test_create_with_a_proof_for_another_payload_is_refused(
    session_maker: Maker, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    purchase = await make_purchase(ann)
    ctx = ctx_for(session_maker, ann.id, str(uuid.uuid4()))
    prepared = await prepare(
        ctx,
        PrepareConfirmationArgs(
            purchase_id=purchase.id,
            reason_category=ReasonCategory.NOT_AS_DESCRIBED,
            reason_text=REASON,
        ),
    )

    # The proof covers REASON; the hash of OTHER_REASON does not match it.
    result = await call(
        ctx,
        "create_refund_request",
        purchase_id=purchase.id,
        reason_category="NOT_AS_DESCRIBED",
        reason_text=OTHER_REASON,
        confirmation_proof=prepared.token,
        idempotency_key="k" * 16,
    )

    assert not result.ok and result.error is not None
    assert result.error.code == "CONFIRMATION_INVALID"
    assert await refund_rows(session_maker) == []


async def test_retrying_create_with_the_same_key_does_not_duplicate(
    session_maker: Maker, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    purchase = await make_purchase(ann)
    thread = await new_thread(session_maker, ann.id)
    ctx = ctx_for(session_maker, ann.id, str(thread))
    prepared = await prepare(
        ctx,
        PrepareConfirmationArgs(
            purchase_id=purchase.id,
            reason_category=ReasonCategory.NOT_AS_DESCRIBED,
            reason_text=REASON,
        ),
    )
    args = {
        "purchase_id": purchase.id,
        "reason_category": "NOT_AS_DESCRIBED",
        "reason_text": REASON,
        "confirmation_proof": prepared.token,
        "idempotency_key": "same-key-1234",
    }

    first = await call(ctx, "create_refund_request", **args)
    second = await call(ctx, "create_refund_request", **args)

    assert first.ok and second.ok
    assert first.data["refund_id"] == second.data["refund_id"]
    assert len(await refund_rows(session_maker)) == 1


async def test_cancel_only_works_in_pending_approval(
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    approved = await make_refund(await make_purchase(ann), status=RefundStatus.APPROVED)
    pending = await make_refund(await make_purchase(ann))
    ctx = ctx_for(session_maker, ann.id, str(uuid.uuid4()))
    async with session_maker() as session:
        versions = {
            r.id: r.version
            for r in (await session.scalars(select(RefundRequest))).all()
        }

    refused = await call(
        ctx, "cancel_refund_request", refund_id=approved, if_match=versions[approved]
    )
    done = await call(
        ctx, "cancel_refund_request", refund_id=pending, if_match=versions[pending]
    )

    assert not refused.ok
    assert done.ok and done.data["status"] == RefundStatus.CANCELLED

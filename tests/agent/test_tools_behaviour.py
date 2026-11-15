"""Tools over the real services: BOLA, error-as-data, minimal projections."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.principal import AGENT_PERMISSIONS, AgentPrincipal
from app.agent.tools.base import ToolContext, ToolResult, run_tool
from app.agent.tools.registry import ALL_TOOLS
from app.domain.enums import ThreadStatus
from app.models.chat import ChatThread
from conftest import MakePurchase, MakeRefund, MakeUser

Maker = async_sessionmaker[AsyncSession]


def ctx_for(maker: Maker, user_id: uuid.UUID, thread_id: str = "") -> ToolContext:
    return ToolContext(
        AgentPrincipal(user_id, "customer", thread_id=thread_id, request_id="t-1"),
        maker,
    )


async def call(ctx: ToolContext, name: str, **args: object) -> ToolResult:
    return await run_tool(ALL_TOOLS[name], ctx, dict(args))


def test_agent_permissions_are_a_subset_of_the_customer() -> None:
    from app.core.permissions import ROLE_PERMISSIONS, RoleName

    assert AGENT_PERMISSIONS <= ROLE_PERMISSIONS[RoleName.CUSTOMER]
    assert {str(p) for p in AGENT_PERMISSIONS} == {
        "purchase:read_own",
        "refund:read_own",
        "policy:read",
        "refund:simulate",
    }


async def test_foreign_purchase_is_purchase_not_found(
    session_maker: Maker, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann, bob = await make_user(), await make_user()
    foreign = await make_purchase(bob)
    result = await call(
        ctx_for(session_maker, ann.id), "get_purchase_details", purchase_id=foreign.id
    )
    assert not result.ok and result.error is not None
    assert result.error.code == "PURCHASE_NOT_FOUND" and not result.error.retryable


async def test_list_my_purchases_only_own_with_minimal_projection(
    session_maker: Maker, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann, bob = await make_user(), await make_user()
    mine = await make_purchase(ann)
    await make_purchase(bob)
    result = await call(ctx_for(session_maker, ann.id), "list_my_purchases")
    assert result.ok
    items = result.data["purchases"]
    assert [i["id"] for i in items] == [str(mine.id)]
    assert "payment_method" not in items[0] and "customer_id" not in items[0]


async def test_invalid_arguments_are_data_not_exceptions(
    session_maker: Maker, make_user: MakeUser
) -> None:
    ann = await make_user()
    ctx = ctx_for(session_maker, ann.id)
    for extra in ({"customer_id": str(uuid.uuid4())}, {"status": "NOPE"}):
        result = await call(ctx, "list_my_purchases", **extra)
        assert not result.ok and result.error is not None
        assert result.error.code == "INVALID_ARGUMENTS"


async def test_refund_status_is_own_only_and_has_no_notes(
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann, bob = await make_user(), await make_user()
    mine = await make_refund(await make_purchase(ann))
    theirs = await make_refund(await make_purchase(bob))
    ctx = ctx_for(session_maker, ann.id)

    listing = await call(ctx, "list_my_refunds")
    assert [r["id"] for r in listing.data["refunds"]] == [str(mine)]

    ok = await call(ctx, "get_refund_status", refund_id=mine)
    assert ok.ok and "timeline" in ok.data
    assert not {"notes", "internal_notes", "payload"} & set(ok.data)

    foreign = await call(ctx, "get_refund_status", refund_id=theirs)
    assert foreign.error is not None and foreign.error.code == "REFUND_NOT_FOUND"


async def test_infrastructure_errors_are_retryable(
    session_maker: Maker, make_user: MakeUser
) -> None:
    class Boom:
        def __call__(self) -> object:
            raise ConnectionError("db down")

    ann = await make_user()
    ctx = ToolContext(AgentPrincipal(ann.id, "customer"), Boom())  # type: ignore[arg-type]
    result = await call(ctx, "list_my_purchases")
    assert result.error is not None
    assert result.error.code == "INFRASTRUCTURE_ERROR" and result.error.retryable


async def test_escalate_marks_own_thread_needs_human(
    session_maker: Maker, make_user: MakeUser
) -> None:
    ann, bob = await make_user(), await make_user()
    async with session_maker() as session:
        own, other = ChatThread(customer_id=ann.id), ChatThread(customer_id=bob.id)
        session.add_all([own, other])
        await session.commit()
        own_id, other_id = own.id, other.id

    ok = await call(
        ctx_for(session_maker, ann.id, str(own_id)), "escalate_to_human", summary="x"
    )
    assert ok.ok
    denied = await call(
        ctx_for(session_maker, ann.id, str(other_id)), "escalate_to_human", summary="x"
    )
    assert denied.error is not None and denied.error.code == "THREAD_NOT_FOUND"

    async with session_maker() as session:
        statuses = dict(
            (await session.execute(select(ChatThread.id, ChatThread.status))).all()
        )
    assert statuses[own_id] == ThreadStatus.NEEDS_HUMAN
    assert statuses[other_id] == ThreadStatus.ACTIVE


@pytest.mark.parametrize("name", ["check_refund_eligibility"])
async def test_eligibility_tool_hides_warnings(
    name: str,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
) -> None:
    ann = await make_user()
    purchase = await make_purchase(ann)
    result = await call(ctx_for(session_maker, ann.id), name, purchase_id=purchase.id)
    assert result.ok and result.data["eligible"] is True
    assert "warnings" not in result.data

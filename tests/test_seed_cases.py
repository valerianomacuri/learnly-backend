"""The 13 edge cases of the challenge (§9), as created by the seed."""

from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli.seed import (
    CASES,
    COURSES,
    purchase_id,
    render_cases,
    seed,
    user_id,
)
from app.core.security import create_access_token
from app.domain.enums import PurchaseStatus, RefundStatus
from app.models.course import Course
from app.models.purchase import CourseProgress, Purchase
from app.models.refund import RefundRequest
from conftest import API

Maker = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


async def run_seed(maker: Maker, now: datetime | None = None) -> Any:
    async with maker() as session:
        report = await seed(session, ttl=timedelta(hours=1), now=now)
        await session.commit()
    return report


async def test_seed_creates_the_13_cases_with_dates_relative_to_now(
    session_maker: Maker,
) -> None:
    assert len(CASES) == 13
    report = await run_seed(session_maker, NOW)
    assert [c.spec.key for c in report.cases] == [c.key for c in CASES]

    async with session_maker() as session:
        for seeded in report.cases:
            spec = seeded.spec
            purchase = await session.get(Purchase, seeded.purchase_id)
            assert purchase is not None, spec.key
            assert purchase.customer_id == user_id(spec.owner)
            assert purchase.purchased_at == NOW - spec.age
            assert purchase.purchase_type is spec.purchase_type
            assert purchase.payment_method is spec.payment_method
            assert purchase.status is spec.status
            progress = await session.scalar(
                select(CourseProgress).where(CourseProgress.purchase_id == purchase.id)
            )
            assert progress is not None
            assert progress.progress_pct == spec.progress
            assert (progress.certificate_issued_at is not None) is spec.certificate


async def test_reseeding_moves_the_purchases_with_the_clock(
    session_maker: Maker,
) -> None:
    await run_seed(session_maker, NOW)
    later = NOW + timedelta(days=3, hours=5)
    report = await run_seed(session_maker, later)

    async with session_maker() as session:
        for seeded in report.cases:
            purchase = await session.get(Purchase, seeded.purchase_id)
            assert purchase is not None
            assert purchase.purchased_at == later - seeded.spec.age


async def test_window_cases_sit_on_the_right_side_of_30_days() -> None:
    by_key = {c.key: c for c in CASES}
    edge = by_key["exact-edge"].age
    assert timedelta(days=30) - timedelta(minutes=30) < edge < timedelta(days=30)
    assert by_key["outside-window"].age > timedelta(days=30)
    assert by_key["high-consumption"].progress > 30
    assert by_key["happy-path"].progress <= 30


async def test_cases_that_need_history_use_their_own_customer(
    session_maker: Maker,
) -> None:
    await run_seed(session_maker, NOW)

    async def refunds(owner: str, status: RefundStatus) -> int:
        async with session_maker() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(RefundRequest)
                    .where(
                        RefundRequest.customer_id == user_id(owner),
                        RefundRequest.status == status,
                    )
                )
                or 0
            )

    completed = RefundStatus.COMPLETED
    assert await refunds("high-frequency", completed) == 3
    assert await refunds("abuse-limit", completed) == 5
    assert await refunds("double-request", RefundStatus.PENDING_APPROVAL) == 1
    # the others are not contaminated: ann only has the "already refunded" one
    assert await refunds("ann", completed) == 1
    assert await refunds("ann", RefundStatus.PENDING_APPROVAL) == 0
    assert await refunds("bob", completed) == 0

    # the history is inside the last 12 months
    async with session_maker() as session:
        oldest = await session.scalar(select(func.min(RefundRequest.created_at)))
    assert oldest is not None and NOW - oldest < timedelta(days=365)


async def test_already_refunded_purchase_has_a_completed_refund(
    session_maker: Maker,
) -> None:
    report = await run_seed(session_maker, NOW)
    pid = next(c.purchase_id for c in report.cases if c.spec.key == "already-refunded")
    async with session_maker() as session:
        purchase = await session.get(Purchase, pid)
        refund = await session.scalar(
            select(RefundRequest).where(RefundRequest.purchase_id == pid)
        )
    assert purchase is not None and purchase.status is PurchaseStatus.REFUNDED
    assert refund is not None and refund.status is RefundStatus.COMPLETED


def test_expectations_are_consistent() -> None:
    for case in CASES:
        assert case.eligible == (not case.reason_codes), case.key
        assert (case.refund_method is not None) == case.eligible, case.key
    assert len({c.key for c in CASES}) == 13
    assert {c.key for c in CASES if c.warnings} == {"high-frequency"}


async def test_the_case_table_lists_every_purchase_and_expectation(
    session_maker: Maker,
) -> None:
    report = await run_seed(session_maker, NOW)
    table = "\n".join(render_cases(report))
    for seeded in report.cases:
        assert str(seeded.purchase_id) in table
        assert seeded.spec.expected in table
    assert purchase_id("happy-path") == report.cases[0].purchase_id


async def test_seeded_courses_exist(session_maker: Maker) -> None:
    await run_seed(session_maker, NOW)
    async with session_maker() as session:
        total = await session.scalar(select(func.count()).select_from(Course))
    assert total == len(COURSES)


async def test_ann_sees_her_cases_through_the_api_and_not_bobs(
    client: AsyncClient, session_maker: Maker
) -> None:
    report = await run_seed(session_maker)
    token = create_access_token(user_id=user_id("ann"))
    ann = {"Authorization": f"Bearer {token}"}

    mine = {str(c.purchase_id) for c in report.cases if c.spec.owner == "ann"}
    listed = await client.get(f"{API}/purchases?limit=100", headers=ann)
    assert {item["id"] for item in listed.json()["items"]} == mine

    foreign = next(c for c in report.cases if c.spec.key == "foreign-purchase")
    resp = await client.get(f"{API}/purchases/{foreign.purchase_id}", headers=ann)
    assert resp.status_code == 404

    edge = next(c for c in report.cases if c.spec.key == "exact-edge")
    detail = await client.get(f"{API}/purchases/{edge.purchase_id}", headers=ann)
    assert detail.json()["within_refund_window"] is True

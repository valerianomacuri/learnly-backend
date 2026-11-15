"""GET /purchases/{id}/refund-eligibility against the 13 seed cases (challenge §9)."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli.seed import CASES, seed, user_id
from app.core.permissions import RoleName
from app.core.security import create_access_token
from app.models.eligibility import EligibilityCheck
from conftest import API, MakePurchase, MakeUser

Maker = async_sessionmaker[AsyncSession]


def url(purchase: uuid.UUID) -> str:
    return f"{API}/purchases/{purchase}/refund-eligibility"


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id=user_id(key))}"}


async def seeded(maker: Maker) -> dict[str, uuid.UUID]:
    async with maker() as session:
        report = await seed(session, ttl=timedelta(hours=1))
        await session.commit()
    return {c.spec.key: c.purchase_id for c in report.cases}


@pytest.mark.parametrize("case", CASES, ids=[c.key for c in CASES])
async def test_seed_cases_through_the_api(
    case: Any, client: AsyncClient, session_maker: Maker
) -> None:
    ids = await seeded(session_maker)
    resp = await client.get(url(ids[case.key]), headers=bearer(case.asker))

    assert resp.status_code == 200
    body = resp.json()
    assert body["eligible"] is case.eligible
    assert body["reason_codes"] == list(case.reason_codes)
    assert body["refund_method"] == (
        case.refund_method.value if case.refund_method else None
    )
    assert body["policy_version"] == "2026-10-01"
    assert body["refund_window_days"] == 30
    # warnings are for the supervisor: the customer schema omits them entirely
    assert "warnings" not in body


async def test_high_frequency_is_recorded_for_the_supervisor_only(
    client: AsyncClient, session_maker: Maker
) -> None:
    ids = await seeded(session_maker)
    resp = await client.get(
        url(ids["high-frequency"]), headers=bearer("high-frequency")
    )
    assert resp.json()["eligible"] is True
    assert "HIGH_REFUND_FREQUENCY" not in resp.text

    async with session_maker() as session:
        check = await session.scalar(
            select(EligibilityCheck).where(
                EligibilityCheck.purchase_id == ids["high-frequency"]
            )
        )
    assert check is not None
    assert check.details["warnings"] == ["HIGH_REFUND_FREQUENCY"]


async def test_foreign_and_missing_purchases_are_indistinguishable(
    client: AsyncClient, session_maker: Maker
) -> None:
    ids = await seeded(session_maker)
    foreign = await client.get(url(ids["foreign-purchase"]), headers=bearer("ann"))
    missing = await client.get(url(uuid.uuid4()), headers=bearer("ann"))

    assert foreign.status_code == missing.status_code == 200
    f, m = foreign.json(), missing.json()
    assert f["reason_codes"] == m["reason_codes"] == ["PURCHASE_NOT_FOUND"]
    assert f["eligible"] is m["eligible"] is False
    for volatile in ("checked_at",):
        f.pop(volatile), m.pop(volatile)
    assert f == m


async def test_every_check_is_persisted(
    client: AsyncClient, session_maker: Maker
) -> None:
    ids = await seeded(session_maker)
    missing = uuid.uuid4()
    await client.get(url(ids["happy-path"]), headers=bearer("ann"))
    await client.get(url(ids["outside-window"]), headers=bearer("ann"))
    await client.get(url(missing), headers=bearer("ann"))

    async with session_maker() as session:
        rows = (
            await session.scalars(
                select(EligibilityCheck)
                .where(EligibilityCheck.customer_id == user_id("ann"))
                .order_by(EligibilityCheck.created_at)
            )
        ).all()
    assert [(r.eligible, r.reason_codes) for r in rows] == [
        (True, []),
        (False, ["OUTSIDE_REFUND_WINDOW"]),
        (False, ["PURCHASE_NOT_FOUND"]),
    ]
    assert {r.source for r in rows} == {"SIMULATION"}
    assert all(r.policy_version == "2026-10-01" for r in rows)
    assert rows[2].purchase_id == missing
    assert rows[1].details["days_since_purchase"] == 31


async def test_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(url(uuid.uuid4()))).status_code == 401


async def test_supervisor_cannot_simulate(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    supervisor = await make_user(RoleName.SUPERVISOR)
    purchase = await make_purchase(await make_user())
    resp = await client.get(url(purchase.id), headers=supervisor.headers)
    assert resp.status_code == 403


async def test_rejects_a_malformed_id(client: AsyncClient, make_user: MakeUser) -> None:
    customer = await make_user()
    resp = await client.get(
        f"{API}/purchases/not-a-uuid/refund-eligibility", headers=customer.headers
    )
    assert resp.status_code == 422

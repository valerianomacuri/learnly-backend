from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import RoleName
from app.models.refund import CreditLedgerEntry
from conftest import API, MakeUser


async def test_me_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(f"{API}/me")).status_code == 401


async def test_me_returns_profile_role_and_zero_balance(
    client: AsyncClient, make_user: MakeUser
) -> None:
    ann = await make_user(email="ann@test.example")
    body = (await client.get(f"{API}/me", headers=ann.headers)).json()
    assert body["id"] == str(ann.id)
    assert body["email"] == "ann@test.example"
    assert body["roles"] == ["customer"]
    assert body["credit_balance"] == 0
    assert "password_hash" not in body


async def test_me_balance_is_the_sum_of_the_ledger(
    client: AsyncClient,
    make_user: MakeUser,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    ann, bob = await make_user(), await make_user()
    async with session_maker() as session:
        session.add_all(
            [
                CreditLedgerEntry(customer_id=ann.id, delta=Decimal("49.99")),
                CreditLedgerEntry(customer_id=ann.id, delta=Decimal("20.01")),
                CreditLedgerEntry(customer_id=ann.id, delta=Decimal("-10.00")),
                CreditLedgerEntry(customer_id=bob.id, delta=Decimal("5.00")),
            ]
        )
        await session.commit()

    assert (await client.get(f"{API}/me", headers=ann.headers)).json()[
        "credit_balance"
    ] == 60.0
    assert (await client.get(f"{API}/me", headers=bob.headers)).json()[
        "credit_balance"
    ] == 5.0


async def test_supervisor_can_read_me(client: AsyncClient, make_user: MakeUser) -> None:
    sup = await make_user(RoleName.SUPERVISOR)
    resp = await client.get(f"{API}/me", headers=sup.headers)
    assert resp.status_code == 200
    assert resp.json()["roles"] == ["supervisor"]

"""User listing: supervisors see everyone, customers are forbidden."""

from typing import Any

from httpx import AsyncClient

from app.core.permissions import RoleName

API = "/api/v1"


async def test_customer_cannot_list_users(client: AsyncClient, make_user: Any) -> None:
    customer = await make_user(RoleName.CUSTOMER)
    resp = await client.get(f"{API}/users", headers=customer.headers)
    assert resp.status_code == 403


async def test_list_users_filters_and_hides_secrets(
    client: AsyncClient, make_user: Any
) -> None:
    supervisor = await make_user(RoleName.SUPERVISOR)
    customer = await make_user(RoleName.CUSTOMER)
    await make_user(RoleName.CUSTOMER, email="findme@example.com")
    resp = await client.get(f"{API}/users", headers=supervisor.headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 3
    assert "password" not in resp.text

    by_email = await client.get(
        f"{API}/users", params={"email": "FINDME"}, headers=supervisor.headers
    )
    assert [u["email"] for u in by_email.json()] == ["findme@example.com"]

    wildcard = await client.get(
        f"{API}/users", params={"email": "%"}, headers=supervisor.headers
    )
    assert wildcard.json() == []

    supervisors = await client.get(
        f"{API}/users", params={"role": "supervisor"}, headers=supervisor.headers
    )
    assert supervisors.status_code == 200
    ids = {u["id"] for u in supervisors.json()}
    assert str(supervisor.id) in ids
    assert str(customer.id) not in ids

    page = await client.get(
        f"{API}/users", params={"limit": 1, "offset": 1}, headers=supervisor.headers
    )
    assert len(page.json()) == 1

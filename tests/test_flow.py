from typing import Any

from httpx import AsyncClient

from app.core.permissions import RoleName

API = "/api/v1"


async def test_duplicate_email_conflicts(client: AsyncClient, make_user: Any) -> None:
    supervisor = (await make_user(RoleName.SUPERVISOR)).headers
    body = {"email": "a@b.com", "name": "Ann", "password": "password-123"}
    first = await client.post(f"{API}/users", json=body, headers=supervisor)
    second = await client.post(f"{API}/users", json=body, headers=supervisor)
    assert first.status_code == 201
    assert second.status_code == 409


async def test_invalid_payload_is_422(client: AsyncClient, make_user: Any) -> None:
    resp = await client.post(
        f"{API}/users",
        json={"email": "x", "name": "Ann", "password": "password-123"},
        headers=(await make_user(RoleName.SUPERVISOR)).headers,
    )
    assert resp.status_code == 422


async def test_roles_in_body_are_rejected(client: AsyncClient, make_user: Any) -> None:
    resp = await client.post(
        f"{API}/users",
        json={
            "email": "a@b.com",
            "name": "Ann",
            "password": "password-123",
            "roles": ["supervisor"],
        },
        headers=(await make_user(RoleName.SUPERVISOR)).headers,
    )
    assert resp.status_code == 422

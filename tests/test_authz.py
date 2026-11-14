"""Authentication, RBAC permission gates and owner isolation (anti-IDOR)."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select

from app.core.permissions import Perm, RoleName
from app.core.security import create_access_token
from app.models.rbac import Permission, Role, role_permissions, user_roles
from app.models.user import User

API = "/api/v1"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/users"),
        ("GET", "/users"),
        ("GET", f"/users/{uuid.uuid4()}"),
        ("PATCH", f"/users/{uuid.uuid4()}"),
    ],
)
async def test_every_route_requires_a_token(
    client: AsyncClient, method: str, path: str
) -> None:
    resp = await client.request(method, f"{API}{path}", json={})
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


async def test_garbage_token_is_401(client: AsyncClient) -> None:
    resp = await client.get(
        f"{API}/users", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert resp.status_code == 401


async def test_token_of_an_unknown_user_is_401(client: AsyncClient) -> None:
    token = create_access_token(user_id=uuid.uuid4())
    resp = await client.get(
        f"{API}/users", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401


async def test_health_stays_public(client: AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "POST",
            "/users",
            {"email": "a@b.com", "name": "Ann", "password": "password-123"},
        ),
        ("GET", "/users", None),
    ],
)
async def test_customer_role_cannot_use_supervisor_routes(
    client: AsyncClient, make_user: Any, method: str, path: str, body: Any
) -> None:
    customer = await make_user(RoleName.CUSTOMER)
    resp = await client.request(
        method, f"{API}{path}", json=body, headers=customer.headers
    )
    assert resp.status_code == 403
    assert "Missing permission" in resp.json()["detail"]


async def test_user_without_roles_has_no_permissions(
    client: AsyncClient, session_maker: Any
) -> None:
    async with session_maker() as session:
        user = User(email="norole@x.com", name="No Role", password_hash="!")
        session.add(user)
        await session.commit()
        token = create_access_token(user_id=user.id)
    headers = {"Authorization": f"Bearer {token}"}
    assert (
        await client.get(f"{API}/users/{user.id}", headers=headers)
    ).status_code == 403
    assert (await client.get(f"{API}/users", headers=headers)).status_code == 403


async def test_permissions_are_read_from_the_database_on_every_request(
    client: AsyncClient, make_user: Any, session_maker: Any
) -> None:
    supervisor = await make_user(RoleName.SUPERVISOR)
    assert (
        await client.get(f"{API}/users", headers=supervisor.headers)
    ).status_code == 200

    async with session_maker() as session:  # revoke user:read_any from supervisors
        role = await session.scalar(
            select(Role).where(Role.name == RoleName.SUPERVISOR.value)
        )
        assert role is not None
        perm = await session.scalar(
            select(Permission).where(Permission.code == Perm.USER_READ_ANY.value)
        )
        assert perm is not None
        await session.execute(
            delete(role_permissions).where(
                role_permissions.c.role_id == role.id,
                role_permissions.c.permission_id == perm.id,
            )
        )
        await session.commit()

    assert (
        await client.get(f"{API}/users", headers=supervisor.headers)
    ).status_code == 403


async def test_removing_a_role_revokes_access_without_a_new_token(
    client: AsyncClient, make_user: Any, session_maker: Any
) -> None:
    supervisor = await make_user(RoleName.SUPERVISOR)
    assert (
        await client.get(f"{API}/users", headers=supervisor.headers)
    ).status_code == 200
    async with session_maker() as session:
        await session.execute(
            delete(user_roles).where(user_roles.c.user_id == supervisor.id)
        )
        await session.commit()
    assert (
        await client.get(f"{API}/users", headers=supervisor.headers)
    ).status_code == 403


async def test_user_with_two_roles_gets_the_union_of_permissions(
    client: AsyncClient, make_user: Any
) -> None:
    both = await make_user(RoleName.CUSTOMER, extra_roles=(RoleName.SUPERVISOR,))
    assert (await client.get(f"{API}/users", headers=both.headers)).status_code == 200
    assert (
        await client.get(f"{API}/users/{both.id}", headers=both.headers)
    ).status_code == 200


async def test_customer_cannot_read_another_customers_user(
    client: AsyncClient, make_user: Any
) -> None:
    mine = await make_user(RoleName.CUSTOMER)
    theirs = await make_user(RoleName.CUSTOMER)
    supervisor = await make_user(RoleName.SUPERVISOR)
    path = f"{API}/users/{theirs.id}"
    assert (await client.get(path, headers=mine.headers)).status_code == 404
    # The owner and any supervisor still see it.
    assert (await client.get(path, headers=theirs.headers)).status_code == 200
    assert (await client.get(path, headers=supervisor.headers)).status_code == 200


async def test_customer_can_read_and_update_only_themselves(
    client: AsyncClient, make_user: Any
) -> None:
    me = await make_user(RoleName.CUSTOMER)
    other = await make_user(RoleName.CUSTOMER)
    ok = await client.patch(
        f"{API}/users/{me.id}", json={"name": "Renamed"}, headers=me.headers
    )
    assert ok.status_code == 200
    assert ok.json()["name"] == "Renamed"
    denied = await client.patch(
        f"{API}/users/{other.id}", json={"name": "Hacked"}, headers=me.headers
    )
    assert denied.status_code == 404

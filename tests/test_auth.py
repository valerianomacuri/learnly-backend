"""POST /auth/login: credentials in, bearer token out, nothing leaked."""

import json
import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.passwords import UNUSABLE_HASH
from app.core.permissions import RoleName
from app.models.user import User

API = "/api/v1"
LOGIN = f"{API}/auth/login"


async def login(client: AsyncClient, email: str, password: str) -> Any:
    return await client.post(LOGIN, json={"email": email, "password": password})


async def test_login_returns_a_token_that_works(
    client: AsyncClient, make_user: Any
) -> None:
    supervisor = await make_user(RoleName.SUPERVISOR)
    resp = await login(client, supervisor.email, supervisor.password)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert (await client.get(f"{API}/users", headers=headers)).status_code == 200


async def test_token_from_login_carries_the_users_permissions(
    client: AsyncClient, make_user: Any
) -> None:
    customer = await make_user(RoleName.CUSTOMER)
    token = (await login(client, customer.email, customer.password)).json()
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    assert (await client.get(f"{API}/users", headers=headers)).status_code == 403
    me = await client.get(f"{API}/users/{customer.id}", headers=headers)
    assert me.status_code == 200


async def test_wrong_password_and_unknown_email_look_identical(
    client: AsyncClient, make_user: Any
) -> None:
    user = await make_user(RoleName.CUSTOMER)
    wrong = await login(client, user.email, "not-the-password")
    unknown = await login(client, "nobody@test.example", "not-the-password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid credentials"}
    assert wrong.headers["www-authenticate"] == unknown.headers["www-authenticate"]


async def test_password_is_case_and_whitespace_sensitive(
    client: AsyncClient, make_user: Any
) -> None:
    user = await make_user(RoleName.CUSTOMER)
    assert (await login(client, user.email, user.password.upper())).status_code == 401
    assert (await login(client, user.email, f" {user.password}")).status_code == 401


async def test_login_rejects_extra_fields_and_missing_password(
    client: AsyncClient, make_user: Any
) -> None:
    user = await make_user(RoleName.CUSTOMER)
    extra = {"email": user.email, "password": user.password, "role": "supervisor"}
    assert (await client.post(LOGIN, json=extra)).status_code == 422
    assert (await client.post(LOGIN, json={"email": user.email})).status_code == 422


async def test_user_without_a_password_cannot_log_in(
    client: AsyncClient, make_user: Any, session_maker: Any
) -> None:
    user = await make_user(RoleName.SUPERVISOR)
    async with session_maker() as session:  # as a pre-password user after migration
        await session.execute(
            update(User).where(User.id == user.id).values(password_hash=UNUSABLE_HASH)
        )
        await session.commit()
    for attempt in (user.password, UNUSABLE_HASH, ""):
        assert (await login(client, user.email, attempt)).status_code in (401, 422)


async def test_created_user_can_log_in_with_the_password_it_was_given(
    client: AsyncClient, make_user: Any
) -> None:
    supervisor = await make_user(RoleName.SUPERVISOR)
    created = await client.post(
        f"{API}/users",
        json={"email": "new@x.com", "name": "New", "password": "  s3cret pass  "},
        headers=supervisor.headers,
    )
    assert created.status_code == 201
    assert (await login(client, "new@x.com", "  s3cret pass  ")).status_code == 200
    assert (await login(client, "new@x.com", "s3cret pass")).status_code == 401


async def test_short_password_is_422(client: AsyncClient, make_user: Any) -> None:
    supervisor = await make_user(RoleName.SUPERVISOR)
    resp = await client.post(
        f"{API}/users",
        json={"email": "new@x.com", "name": "New", "password": "short"},
        headers=supervisor.headers,
    )
    assert resp.status_code == 422
    missing = await client.post(
        f"{API}/users",
        json={"email": "new@x.com", "name": "New"},
        headers=supervisor.headers,
    )
    assert missing.status_code == 422


async def test_password_is_stored_as_an_argon2_hash_and_never_returned(
    client: AsyncClient, make_user: Any, session_maker: Any
) -> None:
    supervisor = await make_user(RoleName.SUPERVISOR)
    password = "plaintext-never-stored"
    created = await client.post(
        f"{API}/users",
        json={"email": "h@x.com", "name": "H", "password": password},
        headers=supervisor.headers,
    )
    assert created.status_code == 201
    fetched = await client.get(
        f"{API}/users/{created.json()['id']}", headers=supervisor.headers
    )
    for response in (created, fetched):
        text = json.dumps(response.json())
        assert "password" not in text and password not in text
        assert "$argon2" not in text
    async with session_maker() as session:
        stored = await session.scalar(
            select(User.password_hash).where(User.email == "h@x.com")
        )
    assert stored is not None
    assert stored.startswith("$argon2id$") and password not in stored


async def test_user_can_change_their_own_password(
    client: AsyncClient, make_user: Any
) -> None:
    user = await make_user(RoleName.CUSTOMER)
    resp = await client.patch(
        f"{API}/users/{user.id}",
        json={"password": "a-brand-new-password"},
        headers=user.headers,
    )
    assert resp.status_code == 200
    assert "password" not in json.dumps(resp.json())
    assert (await login(client, user.email, user.password)).status_code == 401
    assert (await login(client, user.email, "a-brand-new-password")).status_code == 200


async def test_customer_cannot_change_another_users_password(
    client: AsyncClient, make_user: Any
) -> None:
    attacker = await make_user(RoleName.CUSTOMER)
    victim = await make_user(RoleName.CUSTOMER)
    resp = await client.patch(
        f"{API}/users/{victim.id}",
        json={"password": "takeover-password"},
        headers=attacker.headers,
    )
    assert resp.status_code == 404
    assert (await login(client, victim.email, victim.password)).status_code == 200


async def test_login_is_public_and_a_bearer_token_is_not_needed(
    client: AsyncClient,
) -> None:
    resp = await login(client, f"{uuid.uuid4().hex}@x.com", "whatever-password")
    assert resp.status_code == 401  # not "403"/"404": the route exists and is open

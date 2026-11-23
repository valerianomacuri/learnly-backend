"""BOLA / IDOR and role matrix over every route (spec 12).

1. No token -> 401 on every protected route.
2. Wrong role -> 403 on every route restricted to the other role.
3. Someone else's resource looks exactly like a missing one (404, same body).
"""

import re
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))

from agent_helpers import Maker, make_runtime, new_thread, scripted_llm  # noqa: E402
from permissions_table import PERMISSIONS, Access  # noqa: E402

from app.agent.state import Intent  # noqa: E402
from app.core.permissions import RoleName  # noqa: E402
from app.main import app  # noqa: E402
from conftest import API, MakePurchase, MakeRefund, MakeUser  # noqa: E402

PROTECTED = sorted(k for k, v in PERMISSIONS.items() if v is not Access.PUBLIC)
GENERIC_BODY: dict[str, Any] = {}
# Streams never end on their own: they are only checked through their guards.


@pytest.fixture
def runtime(session_maker: Maker) -> AsyncIterator[None]:
    app.state.agent_runtime = make_runtime(
        session_maker, scripted_llm([Intent.POLICY_QUESTION] * 3)
    )
    yield  # type: ignore[misc]
    app.state.agent_runtime = None


def fill(path: str, **ids: Any) -> str:
    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(ids.get(name, uuid.uuid4() if name != "doc_key" else "refund-faq"))

    return re.sub(r"\{(\w+)\}", sub, path)


def request_for(method: str, path: str, headers: dict[str, str]) -> dict[str, Any]:
    extra = {"Idempotency-Key": uuid.uuid4().hex, "If-Match": '"1"'}
    kwargs: dict[str, Any] = {"headers": {**headers, **extra}}
    if method in {"POST", "PATCH"}:
        kwargs["json"] = GENERIC_BODY
    return kwargs


@pytest.mark.parametrize(("method", "path"), PROTECTED)
async def test_no_token_is_401(client: AsyncClient, method: str, path: str) -> None:
    if path.endswith("/stream"):
        pytest.skip("SSE stream: covered by the role matrix below")
    response = await client.request(method, fill(path))
    assert response.status_code == 401, (method, path, response.text)


ROLE_CASES = [
    (method, path, wrong)
    for (method, path), access in sorted(PERMISSIONS.items())
    for wrong in (
        [RoleName.SUPERVISOR]
        if access is Access.CUSTOMER
        else [RoleName.CUSTOMER]
        if access is Access.SUPERVISOR
        else []
    )
]


@pytest.mark.parametrize(("method", "path", "wrong"), ROLE_CASES)
async def test_wrong_role_is_403(
    client: AsyncClient,
    make_user: MakeUser,
    runtime: None,
    method: str,
    path: str,
    wrong: RoleName,
) -> None:
    actor = await make_user(wrong)
    response = await client.request(
        method, fill(path), **request_for(method, path, actor.headers)
    )
    assert response.status_code == 403, (method, path, wrong, response.text)


def strip_ids(body: Any) -> str:
    """Body without ids and timestamps (they differ between two lookups)."""
    text = str(body)
    text = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "ID", text
    )
    return re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:\d{2})?", "TS", text)


async def test_foreign_resources_look_like_missing_ones(
    client: AsyncClient,
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    runtime: None,
) -> None:
    ann = await make_user(RoleName.CUSTOMER)
    bob = await make_user(RoleName.CUSTOMER)
    bob_purchase = await make_purchase(bob, days_ago=5, progress=5)
    bob_refund = await make_refund(await make_purchase(bob, days_ago=6))
    bob_thread = await new_thread(session_maker, bob.id)

    owned: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = [
        ("GET", f"{API}/users/{{user_id}}", {"user_id": bob.id}, {}),
        ("PATCH", f"{API}/users/{{user_id}}", {"user_id": bob.id}, {"name": "Hacked"}),
        (
            "GET",
            f"{API}/purchases/{{purchase_id}}",
            {"purchase_id": bob_purchase.id},
            {},
        ),
        (
            "GET",
            f"{API}/purchases/{{purchase_id}}/refund-eligibility",
            {"purchase_id": bob_purchase.id},
            {},
        ),
        ("GET", f"{API}/refunds/{{refund_id}}", {"refund_id": bob_refund}, {}),
        ("GET", f"{API}/refunds/{{refund_id}}/events", {"refund_id": bob_refund}, {}),
        ("POST", f"{API}/refunds/{{refund_id}}/cancel", {"refund_id": bob_refund}, {}),
        (
            "GET",
            f"{API}/chat/threads/{{thread_id}}/messages",
            {"thread_id": bob_thread},
            {},
        ),
        (
            "GET",
            f"{API}/chat/threads/{{thread_id}}/pending-interrupt",
            {"thread_id": bob_thread},
            {},
        ),
        (
            "POST",
            f"{API}/chat/threads/{{thread_id}}/messages",
            {"thread_id": bob_thread},
            {"content": "hola"},
        ),
        (
            "POST",
            f"{API}/chat/threads/{{thread_id}}/resume",
            {"thread_id": bob_thread},
            {"interrupt_id": "x", "decision": "cancel"},
        ),
        (
            "POST",
            f"{API}/refunds/preview",
            {},
            {
                "purchase_id": str(bob_purchase.id),
                "reason_category": "NOT_AS_DESCRIBED",
                "reason_text": "El curso promete proyectos y solo hay teoría básica.",
            },
        ),
        (
            "POST",
            f"{API}/refunds",
            {},
            {
                "purchase_id": str(bob_purchase.id),
                "reason_category": "NOT_AS_DESCRIBED",
                "reason_text": "El curso promete proyectos y solo hay teoría básica.",
            },
        ),
    ]
    covered = {(m, p.removeprefix(API)) for m, p, _, _ in owned}
    assert covered  # sanity
    for method, template, ids, body in owned:
        headers = {
            **ann.headers,
            "Idempotency-Key": uuid.uuid4().hex,
            "If-Match": '"1"',
        }
        kwargs: dict[str, Any] = {"headers": headers}
        if method in {"POST", "PATCH"}:
            kwargs["json"] = body
        foreign = await client.request(method, fill(template, **ids), **kwargs)
        missing_ids = {k: uuid.uuid4() for k in ids}
        missing_body = {
            k: (str(uuid.uuid4()) if k == "purchase_id" else v) for k, v in body.items()
        }
        if "json" in kwargs:
            kwargs["json"] = missing_body
        missing = await client.request(method, fill(template, **missing_ids), **kwargs)
        label = (method, template)
        assert foreign.status_code == missing.status_code, (label, foreign.text)
        assert strip_ids(foreign.json()) == strip_ids(missing.json()), label
        # Nothing of Bob's leaks through the response.
        assert str(bob.id) not in foreign.text and bob.email not in foreign.text, label


async def test_supervisor_cannot_use_customer_owned_routes(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
) -> None:
    ann = await make_user(RoleName.CUSTOMER)
    sup = await make_user(RoleName.SUPERVISOR)
    purchase = await make_purchase(ann, days_ago=3, progress=1)
    # A supervisor sees purchases only through the refund detail: no *_any scope.
    r = await client.get(f"{API}/purchases/{purchase.id}", headers=sup.headers)
    assert r.status_code == 403
    r = await client.post(
        f"{API}/refunds",
        headers={**sup.headers, "Idempotency-Key": "k"},
        json={
            "purchase_id": str(purchase.id),
            "reason_category": "OTHER",
            "reason_text": "Intento de crear un reembolso como supervisor ajeno.",
        },
    )
    assert r.status_code == 403

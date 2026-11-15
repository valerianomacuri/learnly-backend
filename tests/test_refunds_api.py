"""Spec 03: preview, creation, idempotency, cancellation and ownership."""

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import RoleName
from app.domain.enums import ActorType, RefundStatus
from app.models.eligibility import EligibilityCheck
from app.models.purchase import Purchase
from app.models.refund import RefundEvent, RefundRequest
from app.services.refund_event_service import EventActor, RefundEventService
from conftest import API, MakePurchase, MakeRefund, MakeUser

Maker = async_sessionmaker[AsyncSession]
REASON = "El curso promete proyectos reales y solo hay teoría básica."


def body(purchase_id: uuid.UUID, **over: Any) -> dict[str, Any]:
    data = {
        "purchase_id": str(purchase_id),
        "reason_category": "NOT_AS_DESCRIBED",
        "reason_text": REASON,
    }
    data.update(over)
    return data


async def preview(client: AsyncClient, actor: Any, purchase_id: uuid.UUID) -> Any:
    return await client.post(
        f"{API}/refunds/preview", json=body(purchase_id), headers=actor.headers
    )


async def create(
    client: AsyncClient,
    actor: Any,
    purchase_id: uuid.UUID,
    token: str | None,
    key: str | None = None,
    **over: Any,
) -> Any:
    payload = body(purchase_id, **over)
    if token is not None:
        payload["confirmation_token"] = token
    return await client.post(
        f"{API}/refunds",
        json=payload,
        headers={**actor.headers, "Idempotency-Key": key or uuid.uuid4().hex},
    )


async def count(maker: Maker, model: Any) -> int:
    async with maker() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_happy_path_and_contract(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    p = await make_purchase(ann, days_ago=12, progress=8)

    prev = (await preview(client, ann, p.id)).json()
    assert prev["eligible"] is True
    assert prev["reason_codes"] == []
    assert prev["citations"] == []
    assert prev["purchase_id"] == str(p.id)
    assert prev["amount"] == 49.99
    assert prev["currency"] == "USD"
    assert prev["refund_method"] == "ORIGINAL_PAYMENT"
    assert prev["days_since_purchase"] == 12
    assert prev["refund_window_days"] == 30
    assert prev["progress_pct"] == 8
    assert prev["confirmation_token"]
    assert prev["expires_at"]

    resp = await create(client, ann, p.id, prev["confirmation_token"])
    assert resp.status_code == 201
    refund = resp.json()
    assert refund["status"] == "PENDING_APPROVAL"
    assert refund["version"] == 1
    assert refund["refund_method"] == "ORIGINAL_PAYMENT"
    assert refund["amount"] == 49.99
    assert "warnings" not in refund["eligibility_snapshot"]
    assert "internal_notes" not in refund

    got = await client.get(f"{API}/refunds/{refund['id']}", headers=ann.headers)
    assert got.json() == refund
    listed = await client.get(f"{API}/refunds", headers=ann.headers)
    assert [r["id"] for r in listed.json()["items"]] == [refund["id"]]

    url = f"{API}/refunds/{refund['id']}/events"
    events = await client.get(url, headers=ann.headers)
    assert [e["type"] for e in events.json()] == ["REFUND_CREATED"]
    assert set(events.json()[0]) == {"type", "actor_type", "created_at"}


async def test_ineligible_is_422_and_leaves_a_check_but_no_refund(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    session_maker: Maker,
) -> None:
    ann = await make_user()
    p = await make_purchase(ann, days_ago=45)

    prev = (await preview(client, ann, p.id)).json()
    assert prev["eligible"] is False
    assert prev["confirmation_token"] is None
    assert prev["citations"][0]["doc_key"] == "refund-eligibility"
    checks_before = await count(session_maker, EligibilityCheck)

    resp = await create(client, ann, p.id, "whatever")
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    problem = resp.json()
    assert problem["code"] == "REFUND_NOT_ELIGIBLE"
    assert problem["type"] == "/problems/refund-not-eligible"
    assert problem["reason_codes"] == ["OUTSIDE_REFUND_WINDOW"]
    assert problem["trace_id"] == resp.headers["x-request-id"]

    assert await count(session_maker, RefundRequest) == 0
    assert await count(session_maker, EligibilityCheck) == checks_before + 1


async def test_trace_id_is_propagated(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    p = await make_purchase(ann, days_ago=45)
    resp = await client.post(
        f"{API}/refunds",
        json=body(p.id),
        headers={**ann.headers, "Idempotency-Key": "k1", "X-Request-ID": "trace-123"},
    )
    assert resp.json()["trace_id"] == "trace-123"


async def test_token_errors(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    bob = await make_user()
    p = await make_purchase(ann)
    token = (await preview(client, ann, p.id)).json()["confirmation_token"]

    missing = await create(client, ann, p.id, None)
    assert missing.status_code == 422
    assert missing.json()["code"] == "CONFIRMATION_REQUIRED"

    altered = await create(client, ann, p.id, token, reason_text=REASON + " extra")
    assert altered.json()["code"] == "CONFIRMATION_INVALID"

    garbage = await create(client, ann, p.id, token[:-2] + "xx")
    assert garbage.json()["code"] == "CONFIRMATION_INVALID"

    # the token of another customer (bob previews his own purchase) is useless
    bp = await make_purchase(bob)
    bob_token = (await preview(client, bob, bp.id)).json()["confirmation_token"]
    foreign = await create(client, ann, p.id, bob_token)
    assert foreign.json()["code"] == "CONFIRMATION_INVALID"


async def test_reason_is_validated(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    p = await make_purchase(ann)
    resp = await client.post(
        f"{API}/refunds/preview",
        json=body(p.id, reason_text="muy corto"),
        headers=ann.headers,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "REASON_REQUIRED"


async def test_supervisor_cannot_create(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    p = await make_purchase(ann)
    assert (await preview(client, sup, p.id)).status_code == 403
    assert (await create(client, sup, p.id, "x")).status_code == 403


async def test_expiry_between_preview_and_post_is_422(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    session_maker: Maker,
) -> None:
    ann = await make_user()
    p = await make_purchase(ann, days_ago=29)
    token = (await preview(client, ann, p.id)).json()["confirmation_token"]
    async with session_maker() as session:
        await session.execute(
            update(Purchase)
            .where(Purchase.id == p.id)
            .values(purchased_at=p.purchased_at - timedelta(days=5))
        )
        await session.commit()

    resp = await create(client, ann, p.id, token)
    assert resp.status_code == 422
    assert resp.json()["reason_codes"] == ["OUTSIDE_REFUND_WINDOW"]


async def test_twenty_concurrent_posts_create_one_refund(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    session_maker: Maker,
) -> None:
    ann = await make_user()
    p = await make_purchase(ann)
    token = (await preview(client, ann, p.id)).json()["confirmation_token"]
    key = uuid.uuid4().hex

    responses = await asyncio.gather(
        *(create(client, ann, p.id, token, key) for _ in range(20))
    )

    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["id"] for r in responses}) == 1
    assert len({r.text for r in responses}) == 1
    assert await count(session_maker, RefundRequest) == 1
    assert await count(session_maker, RefundEvent) == 1


async def test_same_key_other_body_is_409(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    p = await make_purchase(ann)
    token = (await preview(client, ann, p.id)).json()["confirmation_token"]
    assert (await create(client, ann, p.id, token, "same")).status_code == 201

    # the key check comes before eligibility, so no token is needed to see the 409
    other_reason = "Otra razón distinta pero igual de larga y válida."
    resp = await create(client, ann, p.id, None, "same", reason_text=other_reason)
    assert resp.status_code == 409
    assert resp.json()["code"] == "IDEMPOTENCY_CONFLICT"


async def test_second_request_for_a_purchase_is_422(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    p = await make_purchase(ann)
    token = (await preview(client, ann, p.id)).json()["confirmation_token"]
    assert (await create(client, ann, p.id, token)).status_code == 201

    resp = await create(client, ann, p.id, token)
    assert resp.status_code == 422
    assert resp.json()["reason_codes"] == ["ACTIVE_REFUND_EXISTS"]


async def test_cancel_and_request_again(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    p = await make_purchase(ann)
    token = (await preview(client, ann, p.id)).json()["confirmation_token"]
    refund = (await create(client, ann, p.id, token)).json()
    cancel = f"{API}/refunds/{refund['id']}/cancel"

    stale = await client.post(cancel, headers={**ann.headers, "If-Match": "7"})
    assert stale.status_code == 409
    assert stale.json()["code"] == "VERSION_CONFLICT"

    cancelled = await client.post(cancel, headers={**ann.headers, "If-Match": '"1"'})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["version"] == 2

    again = await client.post(cancel, headers={**ann.headers, "If-Match": "2"})
    assert again.status_code == 409
    assert again.json()["code"] == "INVALID_TRANSITION"

    url = f"{API}/refunds/{refund['id']}/events"
    events = await client.get(url, headers=ann.headers)
    assert [e["type"] for e in events.json()] == ["REFUND_CREATED", "REFUND_CANCELLED"]

    token2 = (await preview(client, ann, p.id)).json()["confirmation_token"]
    assert token2
    assert (await create(client, ann, p.id, token2)).status_code == 201


async def test_cannot_cancel_a_decided_refund(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    p = await make_purchase(ann)
    refund_id = await make_refund(p, status=RefundStatus.APPROVED)
    resp = await client.post(
        f"{API}/refunds/{refund_id}/cancel", headers={**ann.headers, "If-Match": "1"}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "INVALID_TRANSITION"


async def test_other_customers_refund_is_404(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    bob = await make_user()
    refund_id = await make_refund(await make_purchase(ann))

    for method, suffix in (("GET", ""), ("GET", "/events"), ("POST", "/cancel")):
        resp = await client.request(
            method,
            f"{API}/refunds/{refund_id}{suffix}",
            headers={**bob.headers, "If-Match": "1"},
        )
        assert resp.status_code == 404, suffix
    listed = await client.get(f"{API}/refunds", headers=bob.headers)
    assert listed.json()["items"] == []

    # a foreign purchase in a preview looks like a missing one
    p = await make_purchase(ann)
    prev = (await preview(client, bob, p.id)).json()
    assert prev["eligible"] is False
    assert prev["reason_codes"] == ["PURCHASE_NOT_FOUND"]


async def test_internal_events_are_not_in_the_timeline(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    session_maker: Maker,
) -> None:
    ann = await make_user()
    refund_id = await make_refund(await make_purchase(ann))
    async with session_maker() as session:
        events = RefundEventService(session)
        await events.append(
            refund_id, "REFUND_CREATED", EventActor(ActorType.CUSTOMER, ann.id)
        )
        await events.append(
            refund_id, "INTERNAL_NOTE_ADDED", EventActor(ActorType.SUPERVISOR), {"x": 1}
        )
        await session.commit()
    resp = await client.get(f"{API}/refunds/{refund_id}/events", headers=ann.headers)
    assert [e["type"] for e in resp.json()] == ["REFUND_CREATED"]

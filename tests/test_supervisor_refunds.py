"""Spec 04: supervisor queue, detail, decisions, notes, events and stats."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.permissions import RoleName
from app.core.security import Principal
from app.domain.enums import RefundStatus
from app.models.refund import RefundRequest
from app.models.user import User
from app.services.supervisor_refund_service import (
    QueueFilters,
    SupervisorRefundService,
)
from conftest import API, MakePurchase, MakeRefund, MakeUser

Maker = async_sessionmaker[AsyncSession]
SUP = f"{API}/supervisor"
NOTE = "Customer wrote twice about this; check the course syllabus first."


async def new_refund(
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    customer: Any,
    *,
    amount: str = "49.99",
    status: RefundStatus = RefundStatus.PENDING_APPROVAL,
) -> uuid.UUID:
    """A refund on its own purchase (BR-04: one active request per purchase)."""
    purchase = await make_purchase(customer, amount=amount)
    return await make_refund(purchase, status=status)


async def patch_refund(maker: Maker, refund_id: uuid.UUID, **values: Any) -> None:
    async with maker() as session:
        await session.execute(
            update(RefundRequest).where(RefundRequest.id == refund_id).values(**values)
        )
        await session.commit()


async def decide(
    client: AsyncClient,
    actor: Any,
    refund_id: uuid.UUID,
    action: str = "approve",
    *,
    version: int | str | None = 1,
    note: str | None = None,
) -> Any:
    headers = dict(actor.headers)
    if version is not None:
        headers["If-Match"] = str(version)
    return await client.post(
        f"{SUP}/refunds/{refund_id}/{action}",
        json={"note": note} if note is not None else {},
        headers=headers,
    )


# --------------------------------------------------------------- permissions


async def test_customer_gets_403_on_every_supervisor_route(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    rid = await new_refund(make_purchase, make_refund, ann)
    calls: list[tuple[str, str, dict[str, Any]]] = [
        ("GET", "/refunds", {}),
        ("GET", "/stats", {}),
        ("GET", f"/refunds/{rid}", {}),
        ("GET", f"/refunds/{rid}/events", {}),
        ("GET", f"/refunds/{rid}/notes", {}),
        ("POST", f"/refunds/{rid}/notes", {"json": {"body": "hello"}}),
        ("POST", f"/refunds/{rid}/approve", {"json": {}}),
        ("POST", f"/refunds/{rid}/reject", {"json": {"note": NOTE}}),
    ]
    for method, path, extra in calls:
        resp = await client.request(
            method,
            f"{SUP}{path}",
            headers={**ann.headers, "If-Match": "1"},
            **extra,
        )
        assert resp.status_code == 403, (method, path)

    # and the request was not touched
    sup = await make_user(RoleName.SUPERVISOR)
    detail = await client.get(f"{SUP}/refunds/{rid}", headers=sup.headers)
    assert detail.json()["status"] == "PENDING_APPROVAL"


async def test_anonymous_is_401(client: AsyncClient) -> None:
    assert (await client.get(f"{SUP}/refunds")).status_code == 401


async def test_supervisor_cannot_create_requests_or_use_chat_permissions(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    sup = await make_user(RoleName.SUPERVISOR)
    ann = await make_user()
    p = await make_purchase(ann)
    resp = await client.post(
        f"{API}/refunds",
        json={
            "purchase_id": str(p.id),
            "reason_category": "OTHER",
            "reason_text": "x" * 30,
        },
        headers={**sup.headers, "Idempotency-Key": "k"},
    )
    assert resp.status_code == 403


# ----------------------------------------------------------------- decisions


async def test_approve_moves_state_and_writes_the_event(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    rid = await new_refund(make_purchase, make_refund, ann)

    resp = await decide(client, sup, rid, note="Looks fine")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "APPROVED"
    assert data["version"] == 2
    assert data["decided_by"] == str(sup.id)
    assert data["decided_at"] is not None
    assert data["decision_note"] == "Looks fine"
    assert data["sla_due_at"] is None

    events = (
        await client.get(f"{SUP}/refunds/{rid}/events", headers=sup.headers)
    ).json()
    approved = [e for e in events if e["type"] == "REFUND_APPROVED"]
    assert len(approved) == 1
    assert approved[0]["actor_type"] == "SUPERVISOR"
    assert approved[0]["actor_id"] == str(sup.id)

    # the customer sees the public part of it
    mine = await client.get(f"{API}/refunds/{rid}", headers=ann.headers)
    assert mine.json()["status"] == "APPROVED"


async def test_approve_accepts_no_body_and_weak_etag(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    rid = await new_refund(make_purchase, make_refund, ann)
    resp = await client.post(
        f"{SUP}/refunds/{rid}/approve",
        headers={**sup.headers, "If-Match": 'W/"1"'},
    )
    assert resp.status_code == 200


async def test_reject_requires_a_note_of_at_least_ten_characters(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    rid = await new_refund(make_purchase, make_refund, ann)

    missing = await decide(client, sup, rid, "reject")
    assert missing.status_code == 422
    assert missing.json()["code"] == "DECISION_NOTE_REQUIRED"
    assert (await decide(client, sup, rid, "reject", note="x" * 9)).status_code == 422
    assert (await decide(client, sup, rid, "reject", note=" " * 12)).status_code == 422

    ok = await decide(client, sup, rid, "reject", note="x" * 10)
    assert ok.status_code == 200
    assert ok.json()["status"] == "REJECTED"
    assert ok.json()["decision_note"] == "x" * 10


async def test_self_approval_is_forbidden(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    both = await make_user(RoleName.SUPERVISOR, extra_roles=(RoleName.CUSTOMER,))
    rid = await new_refund(make_purchase, make_refund, both)

    for action in ("approve", "reject"):
        resp = await decide(client, both, rid, action, note=NOTE)
        assert resp.status_code == 403
        assert resp.json()["code"] == "SELF_APPROVAL_FORBIDDEN"

    detail = await client.get(f"{SUP}/refunds/{rid}", headers=both.headers)
    assert detail.json()["status"] == "PENDING_APPROVAL"


async def test_decisions_are_immutable(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    rid = await new_refund(make_purchase, make_refund, ann)
    assert (await decide(client, sup, rid)).status_code == 200

    again = await decide(client, sup, rid, version=2)
    assert again.status_code == 409
    assert again.json()["code"] == "REFUND_NOT_PENDING"
    flip = await decide(client, sup, rid, "reject", version=2, note=NOTE)
    assert flip.status_code == 409

    detail = (await client.get(f"{SUP}/refunds/{rid}", headers=sup.headers)).json()
    assert detail["status"] == "APPROVED"
    assert detail["version"] == 2


async def test_stale_if_match_is_a_409_and_changes_nothing(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    session_maker: Maker,
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    rid = await new_refund(make_purchase, make_refund, ann)
    await patch_refund(session_maker, rid, version=3)

    resp = await decide(client, sup, rid, version=1)
    assert resp.status_code == 409
    assert resp.json()["code"] == "VERSION_CONFLICT"
    detail = (await client.get(f"{SUP}/refunds/{rid}", headers=sup.headers)).json()
    assert detail["status"] == "PENDING_APPROVAL"
    assert (await decide(client, sup, rid, version=3)).status_code == 200


async def test_if_match_is_mandatory_and_validated(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    rid = await new_refund(make_purchase, make_refund, ann)

    missing = await decide(client, sup, rid, version=None)
    assert missing.status_code == 428
    assert missing.json()["code"] == "IF_MATCH_REQUIRED"
    bad = await decide(client, sup, rid, version="abc")
    assert bad.status_code == 400
    assert bad.json()["code"] == "INVALID_IF_MATCH"


async def test_unknown_refund_is_404(client: AsyncClient, make_user: MakeUser) -> None:
    sup = await make_user(RoleName.SUPERVISOR)
    ghost = uuid.uuid4()
    assert (
        await client.get(f"{SUP}/refunds/{ghost}", headers=sup.headers)
    ).status_code == 404
    assert (await decide(client, sup, ghost)).status_code == 404
    assert (
        await client.get(f"{SUP}/refunds/{ghost}/events", headers=sup.headers)
    ).status_code == 404


async def test_two_supervisors_approving_at_once_yield_one_200_and_one_409(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    session_maker: Maker,
) -> None:
    ann = await make_user()
    sup1 = await make_user(RoleName.SUPERVISOR)
    sup2 = await make_user(RoleName.SUPERVISOR)
    rid = await new_refund(make_purchase, make_refund, ann)

    first, second = await asyncio.gather(
        decide(client, sup1, rid), decide(client, sup2, rid)
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]

    async with session_maker() as session:
        refund = await session.get(RefundRequest, rid)
    assert refund is not None
    assert refund.status is RefundStatus.APPROVED
    assert refund.version == 2
    events = (
        await client.get(f"{SUP}/refunds/{rid}/events", headers=sup1.headers)
    ).json()
    assert [e["type"] for e in events].count("REFUND_APPROVED") == 1


async def test_approve_and_reject_racing_leave_a_single_decision(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    sup1 = await make_user(RoleName.SUPERVISOR)
    sup2 = await make_user(RoleName.SUPERVISOR)
    rid = await new_refund(make_purchase, make_refund, ann)

    a, b = await asyncio.gather(
        decide(client, sup1, rid, "approve"),
        decide(client, sup2, rid, "reject", note=NOTE),
    )
    assert sorted([a.status_code, b.status_code]) == [200, 409]


# --------------------------------------------------------------------- notes


async def test_notes_are_supervisor_only_and_leave_an_internal_event(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    rid = await new_refund(make_purchase, make_refund, ann)

    created = await client.post(
        f"{SUP}/refunds/{rid}/notes", json={"body": NOTE}, headers=sup.headers
    )
    assert created.status_code == 201
    assert created.json()["supervisor_id"] == str(sup.id)
    empty = await client.post(
        f"{SUP}/refunds/{rid}/notes", json={"body": ""}, headers=sup.headers
    )
    assert empty.status_code == 422

    listed = (
        await client.get(f"{SUP}/refunds/{rid}/notes", headers=sup.headers)
    ).json()
    assert [n["body"] for n in listed] == [NOTE]
    detail = (await client.get(f"{SUP}/refunds/{rid}", headers=sup.headers)).json()
    assert [n["body"] for n in detail["notes"]] == [NOTE]

    events = (
        await client.get(f"{SUP}/refunds/{rid}/events", headers=sup.headers)
    ).json()
    added = [e for e in events if e["type"] == "NOTE_ADDED"]
    assert len(added) == 1
    assert NOTE not in str(added[0]["payload"])  # the event never copies the text


async def test_notes_never_reach_the_customer(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    rid = await new_refund(make_purchase, make_refund, ann)
    await client.post(
        f"{SUP}/refunds/{rid}/notes", json={"body": NOTE}, headers=sup.headers
    )
    assert (
        await decide(client, sup, rid, note="Approved after review")
    ).status_code == 200

    responses = [
        await client.get(f"{API}/refunds/{rid}", headers=ann.headers),
        await client.get(f"{API}/refunds", headers=ann.headers),
        await client.get(f"{API}/refunds/{rid}/events", headers=ann.headers),
    ]
    for resp in responses:
        assert resp.status_code == 200
        assert NOTE not in resp.text
        assert "NOTE_ADDED" not in resp.text
        assert "internal_notes" not in resp.text
    types = [e["type"] for e in responses[2].json()]
    assert "NOTE_ADDED" not in types
    assert "REFUND_APPROVED" in types


def _models_of(*modules: Any) -> list[type[BaseModel]]:
    found: dict[str, type[BaseModel]] = {}
    for module in modules:
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__ == module.__name__
            ):
                found[f"{module.__name__}.{value.__name__}"] = value
    return list(found.values())


def test_no_customer_schema_can_carry_notes_or_internal_fields() -> None:
    """Serialize every customer schema's JSON schema and look for internal fields."""
    from app.schemas import eligibility, me, purchase, refund

    models = _models_of(refund, purchase, eligibility, me)
    assert len(models) > 5
    forbidden = {"notes", "internal_notes", "note", "payload", "warnings", "body"}
    for model in models:
        text = model.model_json_schema()
        props = _all_property_names(text)
        assert not (props & forbidden), (model.__name__, props & forbidden)


def _all_property_names(node: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            names |= set(props)
        for value in node.values():
            names |= _all_property_names(value)
    elif isinstance(node, list):
        for item in node:
            names |= _all_property_names(item)
    return names


def test_note_added_is_not_a_public_event_type() -> None:
    from app.services.refund_service import PUBLIC_EVENT_TYPES
    from app.services.supervisor_refund_service import EVENT_NOTE_ADDED

    assert EVENT_NOTE_ADDED not in PUBLIC_EVENT_TYPES


# ------------------------------------------------------------------------ PII


def test_mask_email() -> None:
    from app.core.privacy import mask_email

    assert mask_email("leonardo@gmail.com") == "l***@gmail.com"
    assert mask_email("a@b.co") == "a***@b.co"
    assert mask_email("weird@name@x.org") == "w***@x.org"  # splits on the last @
    assert mask_email("@x.org") == "***@x.org"
    assert mask_email("nodomain") == "***"
    assert mask_email("trailing@") == "***"
    assert mask_email("") == "***"


async def test_email_is_masked_in_every_supervisor_response(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann = await make_user(email="leonardo.ann@gmail.com")
    sup = await make_user(RoleName.SUPERVISOR)
    rid = await new_refund(make_purchase, make_refund, ann)
    await client.post(
        f"{SUP}/refunds/{rid}/notes", json={"body": "ok"}, headers=sup.headers
    )

    bodies = [
        await client.get(f"{SUP}/refunds", headers=sup.headers),
        await client.get(
            f"{SUP}/refunds", params={"customer": "leonardo.ann"}, headers=sup.headers
        ),
        await client.get(f"{SUP}/refunds/{rid}", headers=sup.headers),
        await client.get(f"{SUP}/refunds/{rid}/events", headers=sup.headers),
        await decide(client, sup, rid),
    ]
    for resp in bodies:
        assert resp.status_code == 200
        assert "leonardo.ann@gmail.com" not in resp.text
    listed = bodies[1].json()["items"]
    assert [i["customer"]["email"] for i in listed] == ["l***@gmail.com"]


# --------------------------------------------------------------- detail/flags


async def test_detail_has_snapshot_purchase_history_and_flags(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    session_maker: Maker,
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    old = await new_refund(
        make_purchase, make_refund, ann, status=RefundStatus.REJECTED
    )
    purchase = await make_purchase(ann, progress=12, amount="59.00")
    rid = await make_refund(purchase)
    await patch_refund(
        session_maker,
        rid,
        eligibility_snapshot={
            "eligible": True,
            "reason_codes": [],
            "warnings": ["HIGH_REFUND_FREQUENCY"],
            "days_since_purchase": 5,
            "refund_window_days": 30,
            "progress_pct": 12,
            "policy_version": "test",
        },
    )

    detail = (await client.get(f"{SUP}/refunds/{rid}", headers=sup.headers)).json()
    assert detail["eligibility"]["warnings"] == ["HIGH_REFUND_FREQUENCY"]
    assert detail["flags"] == {
        "sla_breached": False,
        "high_risk": True,
        "warnings": ["HIGH_REFUND_FREQUENCY"],
    }
    assert detail["purchase"]["progress"]["progress_pct"] == 12
    assert detail["purchase"]["amount"] == 59.0
    assert detail["customer"] == {
        "id": str(ann.id),
        "name": "Test User",
        "email": detail["customer"]["email"],
    }
    assert "@" in detail["customer"]["email"] and "***" in detail["customer"]["email"]
    assert detail["customer_history"]["total"] == 2
    assert detail["customer_history"]["by_status"] == {
        "PENDING_APPROVAL": 1,
        "REJECTED": 1,
    }
    assert {r["id"] for r in detail["customer_history"]["recent"]} == {
        str(rid),
        str(old),
    }
    assert detail["sla_due_at"] is not None
    assert detail["notes"] == []


async def test_partial_snapshots_do_not_break_the_views(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    """Seeded rows store `{"seed": true}`: every field of the snapshot is optional."""
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    purchase = await make_purchase(ann)
    rid = await make_refund(purchase, snapshot={"seed": True})
    assert (await client.get(f"{SUP}/refunds", headers=sup.headers)).status_code == 200
    detail = await client.get(f"{SUP}/refunds/{rid}", headers=sup.headers)
    assert detail.status_code == 200
    assert detail.json()["flags"]["high_risk"] is False


# ------------------------------------------------------------ queue + filters


async def test_filters(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    session_maker: Maker,
) -> None:
    sup = await make_user(RoleName.SUPERVISOR)
    ann = await make_user(email="ann.filter@example.com")
    bob = await make_user(email="bob.filter@example.com")
    async with session_maker() as session:
        await session.execute(
            update(User).where(User.id == ann.id).values(name="Ann Rivera")
        )
        await session.execute(
            update(User).where(User.id == bob.id).values(name="Bob Stone")
        )
        await session.commit()

    a1 = await new_refund(make_purchase, make_refund, ann)
    a2 = await new_refund(make_purchase, make_refund, ann, status=RefundStatus.APPROVED)
    b1 = await new_refund(make_purchase, make_refund, bob)
    now = datetime.now(UTC)
    await patch_refund(
        session_maker,
        a1,
        reason_category="TECHNICAL_ISSUES",
        channel="CHAT",
        created_at=now - timedelta(days=10),
    )
    await patch_refund(session_maker, a2, created_at=now - timedelta(days=5))
    await patch_refund(
        session_maker,
        b1,
        eligibility_snapshot={"warnings": ["HIGH_REFUND_FREQUENCY"]},
        created_at=now - timedelta(hours=100),
    )

    async def ids(**params: Any) -> list[str]:
        resp = await client.get(f"{SUP}/refunds", params=params, headers=sup.headers)
        assert resp.status_code == 200, resp.text
        return [i["id"] for i in resp.json()["items"]]

    assert set(await ids()) == {str(a1), str(a2), str(b1)}
    assert await ids(status="APPROVED") == [str(a2)]
    assert await ids(channel="CHAT") == [str(a1)]
    assert await ids(reason_category="TECHNICAL_ISSUES") == [str(a1)]
    assert await ids(high_risk="true") == [str(b1)]
    assert set(await ids(high_risk="false")) == {str(a1), str(a2)}
    assert set(await ids(sla_breached="true")) == {str(a1), str(b1)}  # pending > 48 h
    assert await ids(sla_breached="false") == [str(a2)]
    assert set(await ids(customer="ann")) == {str(a1), str(a2)}
    assert await ids(customer="BOB.FILTER") == [str(b1)]  # email, case-insensitive
    assert await ids(customer="%") == []  # wildcards are escaped
    recent = await ids(created_from=(now - timedelta(days=6)).isoformat())
    assert set(recent) == {str(a2), str(b1)}
    window = await ids(
        created_from=(now - timedelta(days=11)).isoformat(),
        created_to=(now - timedelta(days=6)).isoformat(),
    )
    assert window == [str(a1)]
    assert await ids(status="APPROVED", customer="bob") == []


async def test_sort_and_cursor_pagination(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    session_maker: Maker,
) -> None:
    sup = await make_user(RoleName.SUPERVISOR)
    ann = await make_user()
    now = datetime.now(UTC)
    amounts = ["10.00", "30.00", "20.00", "50.00", "40.00"]
    created: list[uuid.UUID] = []
    for i, amount in enumerate(amounts):
        rid = await new_refund(make_purchase, make_refund, ann, amount=amount)
        await patch_refund(session_maker, rid, created_at=now - timedelta(hours=i))
        created.append(rid)  # i=0 is the newest

    async def walk(**params: Any) -> list[str]:
        out: list[str] = []
        cursor: str | None = None
        while True:
            q = {**params, "limit": 2, **({"cursor": cursor} if cursor else {})}
            resp = await client.get(f"{SUP}/refunds", params=q, headers=sup.headers)
            assert resp.status_code == 200, resp.text
            page = resp.json()
            out += [i["id"] for i in page["items"]]
            cursor = page["next_cursor"]
            if cursor is None:
                return out

    newest_first = [str(r) for r in created]
    assert await walk() == newest_first
    assert await walk(order="asc") == newest_first[::-1]
    assert await walk(sort="sla") == newest_first[::-1]  # most overdue first
    by_amount = [str(created[i]) for i in (3, 4, 1, 2, 0)]  # 50, 40, 30, 20, 10
    assert await walk(sort="amount") == by_amount
    assert await walk(sort="amount", order="asc") == by_amount[::-1]


async def test_cursor_is_stable_with_equal_sort_keys(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    sup = await make_user(RoleName.SUPERVISOR)
    ann = await make_user()
    for _ in range(5):  # same amount: only the id breaks the tie
        await new_refund(make_purchase, make_refund, ann, amount="25.00")
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        params: dict[str, Any] = {"sort": "amount", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = (
            await client.get(f"{SUP}/refunds", params=params, headers=sup.headers)
        ).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 5 and len(set(seen)) == 5


async def test_bad_cursor_and_limits_are_rejected(
    client: AsyncClient, make_user: MakeUser
) -> None:
    sup = await make_user(RoleName.SUPERVISOR)
    bad = await client.get(
        f"{SUP}/refunds", params={"cursor": "nope"}, headers=sup.headers
    )
    assert bad.status_code == 422
    assert (
        await client.get(f"{SUP}/refunds", params={"limit": 0}, headers=sup.headers)
    ).status_code == 422
    assert (
        await client.get(f"{SUP}/refunds", params={"sort": "x"}, headers=sup.headers)
    ).status_code == 422


# ----------------------------------------------------------------------- SLA

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


async def test_sla_breached_uses_the_injected_clock(
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    session_maker: Maker,
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    fresh = await new_refund(make_purchase, make_refund, ann)
    edge = await new_refund(make_purchase, make_refund, ann)
    late = await new_refund(make_purchase, make_refund, ann)
    decided = await new_refund(
        make_purchase, make_refund, ann, status=RefundStatus.APPROVED
    )
    await patch_refund(session_maker, fresh, created_at=NOW - timedelta(hours=47))
    await patch_refund(session_maker, edge, created_at=NOW - timedelta(hours=48))
    await patch_refund(
        session_maker, late, created_at=NOW - timedelta(hours=48, seconds=1)
    )
    await patch_refund(session_maker, decided, created_at=NOW - timedelta(hours=500))

    principal = Principal.for_roles(sup.id, [RoleName.SUPERVISOR])
    async with session_maker() as session:
        service = SupervisorRefundService(session, clock=lambda: NOW, sla_hours=48)
        breached = await service.list_queue(principal, QueueFilters(sla_breached=True))
        ok = await service.list_queue(principal, QueueFilters(sla_breached=False))
        assert [i.id for i in breached.items] == [late]
        assert breached.items[0].flags.sla_breached is True
        assert {i.id for i in ok.items} == {fresh, edge, decided}
        flags = {i.id: i.flags.sla_breached for i in ok.items}
        assert flags == {fresh: False, edge: False, decided: False}
        stats = await service.stats(principal)
        assert stats.sla_breached == 1 and stats.pending == 3

        # a stricter SLA moves the line: nothing is stored, so nothing to migrate
        strict = SupervisorRefundService(session, clock=lambda: NOW, sla_hours=24)
        wide = await strict.list_queue(principal, QueueFilters(sla_breached=True))
        assert {i.id for i in wide.items} == {fresh, edge, late}
        loose = SupervisorRefundService(session, clock=lambda: NOW, sla_hours=72)
        none = await loose.list_queue(principal, QueueFilters(sla_breached=True))
        assert none.items == []

        # a later clock breaches the fresh one too, and it never auto-approves
        later = SupervisorRefundService(
            session, clock=lambda: NOW + timedelta(days=30), sla_hours=48
        )
        every = await later.list_queue(principal, QueueFilters(sla_breached=True))
        assert {i.id for i in every.items} == {fresh, edge, late}
        refund = await session.get(RefundRequest, late)
        assert refund is not None and refund.status is RefundStatus.PENDING_APPROVAL


async def test_service_rechecks_permissions(
    make_user: MakeUser, session_maker: Maker
) -> None:
    from app.services.errors import ForbiddenError

    ann = await make_user()
    customer = Principal.for_roles(ann.id, [RoleName.CUSTOMER])
    async with session_maker() as session:
        service = SupervisorRefundService(session)
        with pytest.raises(ForbiddenError):
            await service.list_queue(customer, QueueFilters())
        with pytest.raises(ForbiddenError):
            await service.approve(customer, uuid.uuid4(), if_match=1)
        with pytest.raises(ForbiddenError):
            await service.stats(customer)


# --------------------------------------------------------------------- stats


async def test_stats(
    client: AsyncClient,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    session_maker: Maker,
) -> None:
    ann = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)

    empty = (await client.get(f"{SUP}/stats", headers=sup.headers)).json()
    assert empty == {
        "pending": 0,
        "sla_breached": 0,
        "avg_decision_seconds": None,
        "approval_rate": None,
        "top_reasons": [],
    }

    r1 = await new_refund(make_purchase, make_refund, ann)
    r2 = await new_refund(make_purchase, make_refund, ann)
    r3 = await new_refund(make_purchase, make_refund, ann)
    r4 = await new_refund(make_purchase, make_refund, ann)
    r5 = await new_refund(make_purchase, make_refund, ann)
    await patch_refund(session_maker, r5, reason_category="TECHNICAL_ISSUES")
    await patch_refund(
        session_maker, r4, created_at=datetime.now(UTC) - timedelta(hours=100)
    )
    assert (await decide(client, sup, r1)).status_code == 200
    assert (await decide(client, sup, r2, "reject", note=NOTE)).status_code == 200
    assert (await decide(client, sup, r3)).status_code == 200

    stats = (await client.get(f"{SUP}/stats", headers=sup.headers)).json()
    assert stats["pending"] == 2
    assert stats["sla_breached"] == 1
    assert stats["approval_rate"] == pytest.approx(2 / 3)
    assert (
        stats["avg_decision_seconds"] is not None and stats["avg_decision_seconds"] >= 0
    )
    assert stats["top_reasons"][0] == {
        "reason_category": "NOT_AS_DESCRIBED",
        "count": 4,
    }
    assert stats["top_reasons"][1] == {
        "reason_category": "TECHNICAL_ISSUES",
        "count": 1,
    }


# ---------------------------------------------------------------------- seed


async def test_seed_fills_the_dashboard_and_stays_idempotent(
    session_maker: Maker,
) -> None:
    from sqlalchemy import func, select

    from app.cli.seed import seed, user_id
    from app.models.refund import RefundEvent

    async def run() -> None:
        async with session_maker() as session:
            await seed(session, ttl=timedelta(hours=1), now=NOW)
            await session.commit()

    async def totals() -> tuple[int, int]:
        async with session_maker() as session:
            requests = await session.scalar(
                select(func.count()).select_from(RefundRequest)
            )
            events = await session.scalar(select(func.count()).select_from(RefundEvent))
        return int(requests or 0), int(events or 0)

    await run()
    first = await totals()
    await run()
    assert await totals() == first

    principal = Principal.for_roles(user_id("supervisor-1"), [RoleName.SUPERVISOR])
    async with session_maker() as session:
        service = SupervisorRefundService(session, clock=lambda: NOW, sla_hours=48)
        pending = await service.list_queue(
            principal, QueueFilters(status=RefundStatus.PENDING_APPROVAL)
        )
        breached = await service.list_queue(principal, QueueFilters(sla_breached=True))
        risky = await service.list_queue(principal, QueueFilters(high_risk=True))
        stats = await service.stats(principal)

    assert len(pending.items) == 6  # 5 queue demos + the "double-request" case
    assert len(breached.items) == 2
    assert len(risky.items) == 1 and risky.items[0].flags.high_risk
    assert stats.pending == 6 and stats.sla_breached == 2
    assert stats.approval_rate is not None and 0 < stats.approval_rate < 1
    assert all(
        "@" in i.customer.email and "***" in i.customer.email for i in pending.items
    )

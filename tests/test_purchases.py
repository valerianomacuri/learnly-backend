from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from app.core.permissions import RoleName
from app.domain.enums import PaymentMethod, PurchaseStatus
from conftest import API, MakeCourse, MakePurchase, MakeUser


async def test_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(f"{API}/purchases")).status_code == 401


async def test_supervisor_cannot_list_or_read_purchases(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    supervisor = await make_user(RoleName.SUPERVISOR)
    purchase = await make_purchase(await make_user())

    listing = await client.get(f"{API}/purchases", headers=supervisor.headers)
    detail = await client.get(
        f"{API}/purchases/{purchase.id}", headers=supervisor.headers
    )
    assert listing.status_code == 403
    assert detail.status_code == 403


async def test_list_returns_only_own_purchases_newest_first(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann, bob = await make_user(), await make_user()
    old = await make_purchase(ann, days_ago=20)
    new = await make_purchase(ann, days_ago=2)
    await make_purchase(bob, days_ago=1)

    resp = await client.get(f"{API}/purchases", headers=ann.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert [p["id"] for p in body["items"]] == [str(new.id), str(old.id)]
    assert body["next_cursor"] is None


async def test_purchase_payload(
    client: AsyncClient,
    make_user: MakeUser,
    make_course: MakeCourse,
    make_purchase: MakePurchase,
) -> None:
    ann = await make_user()
    course = await make_course("FastAPI in Depth", instructor_name="Ada")
    purchase = await make_purchase(
        ann, days_ago=10, course_id=course, payment_method=PaymentMethod.PAYPAL
    )

    item = (await client.get(f"{API}/purchases", headers=ann.headers)).json()["items"][
        0
    ]
    assert item["course"] == {
        "id": str(course),
        "title": "FastAPI in Depth",
        "instructor_name": "Ada",
    }
    assert item["amount"] == 49.99 and item["currency"] == "USD"
    assert item["payment_method"] == "PAYPAL"
    assert item["purchase_type"] == "INDIVIDUAL"
    assert item["status"] == "ACTIVE"
    assert item["within_refund_window"] is True
    deadline = datetime.fromisoformat(item["refund_deadline"])
    assert deadline == purchase.purchased_at + timedelta(days=30)
    assert "customer_id" not in item


async def test_filter_by_status(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    active = await make_purchase(ann)
    refunded = await make_purchase(ann, status=PurchaseStatus.REFUNDED)

    async def ids(query: str) -> list[str]:
        resp = await client.get(f"{API}/purchases{query}", headers=ann.headers)
        return [p["id"] for p in resp.json()["items"]]

    assert await ids("?status=ACTIVE") == [str(active.id)]
    assert await ids("?status=REFUNDED") == [str(refunded.id)]
    assert (
        await client.get(f"{API}/purchases?status=BOGUS", headers=ann.headers)
    ).status_code == 422


async def test_filter_within_window(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    inside = await make_purchase(ann, days_ago=29)
    outside = await make_purchase(ann, days_ago=31)

    async def ids(query: str) -> list[str]:
        resp = await client.get(f"{API}/purchases{query}", headers=ann.headers)
        return [p["id"] for p in resp.json()["items"]]

    assert await ids("?within_window=true") == [str(inside.id)]
    assert await ids("?within_window=false") == [str(outside.id)]
    assert await ids("") == [str(inside.id), str(outside.id)]
    flags = {
        p["id"]: p["within_refund_window"]
        for p in (await client.get(f"{API}/purchases", headers=ann.headers)).json()[
            "items"
        ]
    }
    assert flags == {str(inside.id): True, str(outside.id): False}


async def test_cursor_pagination_walks_every_purchase_once(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    same_moment = datetime.now(UTC) - timedelta(days=3)
    created = [await make_purchase(ann, days_ago=d) for d in (1, 2, 4, 5)]
    # two purchases with the same timestamp: `id` must break the tie
    created += [await make_purchase(ann, purchased_at=same_moment) for _ in range(2)]

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str | int] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = (
            await client.get(f"{API}/purchases", params=params, headers=ann.headers)
        ).json()
        seen += [p["id"] for p in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert len(seen) == len(set(seen)) == len(created)
    assert set(seen) == {str(p.id) for p in created}


async def test_exact_page_size_has_no_next_cursor(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    for days in (1, 2):
        await make_purchase(ann, days_ago=days)
    body = (await client.get(f"{API}/purchases?limit=2", headers=ann.headers)).json()
    assert len(body["items"]) == 2 and body["next_cursor"] is None


async def test_invalid_cursor_and_limit(
    client: AsyncClient, make_user: MakeUser
) -> None:
    ann = await make_user()
    assert (
        await client.get(f"{API}/purchases?cursor=not-a-cursor", headers=ann.headers)
    ).status_code == 422
    for limit in (0, 101):
        resp = await client.get(f"{API}/purchases?limit={limit}", headers=ann.headers)
        assert resp.status_code == 422


async def test_detail_includes_progress_and_deadline(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    purchase = await make_purchase(ann, days_ago=8, progress=40, certificate=True)

    resp = await client.get(f"{API}/purchases/{purchase.id}", headers=ann.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(purchase.id)
    assert body["progress"]["progress_pct"] == 40
    assert body["progress"]["lectures_completed"] == 20
    assert body["progress"]["certificate_issued_at"] is not None
    assert datetime.fromisoformat(body["refund_deadline"]) == (
        purchase.purchased_at + timedelta(days=30)
    )


async def test_detail_without_progress_row(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    purchase = await make_purchase(ann, progress=None)
    body = (
        await client.get(f"{API}/purchases/{purchase.id}", headers=ann.headers)
    ).json()
    assert body["progress"] is None


async def test_foreign_and_missing_purchases_are_indistinguishable(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    """BOLA: asking for someone else's purchase looks exactly like a missing one."""
    ann, bob = await make_user(), await make_user()
    bobs = await make_purchase(bob)

    foreign = await client.get(f"{API}/purchases/{bobs.id}", headers=ann.headers)
    missing = await client.get(
        f"{API}/purchases/00000000-0000-4000-8000-000000000000", headers=ann.headers
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.content == missing.content  # same body, byte for byte
    assert foreign.headers["content-type"] == missing.headers["content-type"]

    # the owner still sees it
    own = await client.get(f"{API}/purchases/{bobs.id}", headers=bob.headers)
    assert own.status_code == 200


async def test_malformed_purchase_id_is_a_validation_error(
    client: AsyncClient, make_user: MakeUser
) -> None:
    ann = await make_user()
    resp = await client.get(f"{API}/purchases/not-a-uuid", headers=ann.headers)
    assert resp.status_code == 422

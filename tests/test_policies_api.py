"""Spec 06: policy viewer endpoints (ACTIVE documents with their Markdown)."""

from httpx import AsyncClient

from app.core.permissions import RoleName
from conftest import API, MakeUser


async def test_list_returns_active_documents_without_body(
    client: AsyncClient, make_user: MakeUser
) -> None:
    ann = await make_user()
    response = await client.get(f"{API}/policies", headers=ann.headers)
    assert response.status_code == 200
    docs = response.json()
    keys = {d["doc_key"] for d in docs}
    assert {"refund-eligibility", "refund-faq"} <= keys
    for doc in docs:
        assert doc["version"] and doc["effective_date"] and doc["title"]
        assert "content" not in doc


async def test_detail_has_markdown_without_front_matter(
    client: AsyncClient, make_user: MakeUser
) -> None:
    ann = await make_user()
    response = await client.get(f"{API}/policies/refund-faq", headers=ann.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["doc_key"] == "refund-faq"
    assert body["content"].startswith("# ")
    assert "doc_key:" not in body["content"]


async def test_supervisor_can_read_too(
    client: AsyncClient, make_user: MakeUser
) -> None:
    sup = await make_user(RoleName.SUPERVISOR)
    response = await client.get(f"{API}/policies/refund-faq", headers=sup.headers)
    assert response.status_code == 200


async def test_unknown_document_is_404(
    client: AsyncClient, make_user: MakeUser
) -> None:
    ann = await make_user()
    response = await client.get(f"{API}/policies/nope", headers=ann.headers)
    assert response.status_code == 404


async def test_requires_authentication(client: AsyncClient) -> None:
    assert (await client.get(f"{API}/policies")).status_code == 401

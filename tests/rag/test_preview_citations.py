"""`/refunds/preview` cites policy sections without LLM or embeddings (spec 07)."""

from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient

from app.rag import embeddings, llm
from conftest import API, MakeUser

MakePurchase = Callable[..., Any]


@pytest.fixture(autouse=True)
def providers_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("preview must not touch the LLM or embeddings")

    monkeypatch.setattr(embeddings, "get_embedder", boom)
    monkeypatch.setattr(llm, "get_chat_model", boom)
    monkeypatch.setattr(embeddings.OpenAIEmbedder, "__init__", boom)
    monkeypatch.setattr(llm.OpenAIChat, "__init__", boom)


async def preview(client: AsyncClient, actor: Any, purchase_id: object) -> Any:
    return await client.post(
        f"{API}/refunds/preview",
        headers=actor.headers,
        json={
            "purchase_id": str(purchase_id),
            "reason_category": "OTHER",
            "reason_text": "No me gustó el contenido del curso",
        },
    )


async def test_eligible_preview_cites_window_and_method(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    purchase = await make_purchase(ann, days_ago=5, progress=5)
    body = (await preview(client, ann, purchase.id)).json()
    assert body["eligible"] is True
    assert body["citations"] == [
        {
            "doc_key": "refund-eligibility",
            "section_path": "Elegibilidad > Ventana de 30 días",
        },
        {
            "doc_key": "refund-methods",
            "section_path": (
                "Métodos de reembolso > Reembolso al medio de pago original"
            ),
        },
    ]


async def test_ineligible_preview_cites_the_reason_sections(
    client: AsyncClient, make_user: MakeUser, make_purchase: MakePurchase
) -> None:
    ann = await make_user()
    purchase = await make_purchase(ann, days_ago=45)
    body = (await preview(client, ann, purchase.id)).json()
    assert body["eligible"] is False
    assert {
        "doc_key": "refund-eligibility",
        "section_path": "Elegibilidad > Ventana de 30 días",
    } in body["citations"]

"""Chat API of the refund flow with HITL (spec 10): resume, pending-interrupt, 409s."""

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import AsyncClient

sys.path.insert(0, str(Path(__file__).parent / "agent"))

from agent_helpers import make_runtime, refund_flow_llm  # noqa: E402

from app.agent.nodes.collect_refund_info import CollectResult  # noqa: E402
from app.agent.runtime import AgentRuntime  # noqa: E402
from app.agent.state import Intent  # noqa: E402
from app.core.permissions import RoleName  # noqa: E402
from app.main import app  # noqa: E402
from conftest import API, MakePurchase, MakeUser  # noqa: E402
from test_chat_api import create_thread, parse_sse  # noqa: E402

REASON = "El contenido del curso no corresponde con la descripción publicada."
SUP = f"{API}/supervisor"


@pytest.fixture
async def hitl_runtime(
    session_maker, make_user: MakeUser, make_purchase: MakePurchase
) -> AsyncIterator[AgentRuntime]:  # type: ignore[no-untyped-def]
    ann = await make_user()
    purchase = await make_purchase(ann)
    llm = refund_flow_llm(
        Intent.REFUND_REQUEST,
        [
            CollectResult(
                purchase_id=str(purchase.id),
                reason_category="NOT_AS_DESCRIBED",
                reason_text=REASON,
            )
        ],
    )
    rt = make_runtime(session_maker, llm)
    app.state.agent_runtime = rt
    app.state.hitl_customer = ann
    yield rt
    app.state.agent_runtime = None


async def test_refund_flow_over_http(
    client: AsyncClient,
    make_user: MakeUser,
    hitl_runtime: AgentRuntime,
) -> None:
    ann = app.state.hitl_customer
    bob = await make_user()
    sup = await make_user(RoleName.SUPERVISOR)
    thread = await create_thread(client, ann.headers)
    base = f"{API}/chat/threads/{thread}"

    sent = await client.post(
        f"{base}/messages", headers=ann.headers, json={"content": "Quiero un reembolso"}
    )
    frames = parse_sse(sent.text)
    assert frames[-1]["event"] == "interrupt"
    card = frames[-1]["data"]
    assert card["kind"] == "refund_confirmation"

    # Reloading the page recovers the same card.
    pending = await client.get(f"{base}/pending-interrupt", headers=ann.headers)
    assert pending.status_code == 200 and pending.json() == card
    history = await client.get(f"{base}/messages", headers=ann.headers)
    assert history.json()[-1]["interrupt"] == card

    # A free message does not make the card disappear.
    blocked = await client.post(
        f"{base}/messages", headers=ann.headers, json={"content": "hola"}
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "INTERRUPT_PENDING"
    assert blocked.json()["interrupt"] == card

    # Wrong id, and someone else's thread.
    resume_url = f"{base}/resume"
    wrong = await client.post(
        resume_url,
        headers=ann.headers,
        json={"interrupt_id": "nope", "decision": "confirm"},
    )
    assert wrong.status_code == 409
    foreign = await client.post(
        resume_url,
        headers=bob.headers,
        json={"interrupt_id": card["interrupt_id"], "decision": "confirm"},
    )
    assert foreign.status_code == 404
    assert (await client.get(f"{SUP}/refunds", headers=sup.headers)).json()[
        "items"
    ] == []

    body = {"interrupt_id": card["interrupt_id"], "decision": "confirm"}
    done = await client.post(resume_url, headers=ann.headers, json=body)
    assert done.status_code == 200
    assert parse_sse(done.text)[-1]["event"] == "final"

    # Resolved: no pending card, and replaying the resume is a 409.
    assert (
        await client.get(f"{base}/pending-interrupt", headers=ann.headers)
    ).status_code == 204
    again = await client.post(resume_url, headers=ann.headers, json=body)
    assert again.status_code == 409

    queue = await client.get(
        f"{SUP}/refunds", params={"channel": "CHAT"}, headers=sup.headers
    )
    assert len(queue.json()["items"]) == 1


async def test_resume_validates_the_body(
    client: AsyncClient, hitl_runtime: AgentRuntime
) -> None:
    ann = app.state.hitl_customer
    thread = await create_thread(client, ann.headers)
    response = await client.post(
        f"{API}/chat/threads/{thread}/resume",
        headers=ann.headers,
        json={"interrupt_id": "x", "decision": "approve"},
    )
    assert response.status_code == 422


async def test_pending_interrupt_is_204_when_nothing_waits(
    client: AsyncClient, hitl_runtime: AgentRuntime
) -> None:
    ann = app.state.hitl_customer
    thread = await create_thread(client, ann.headers)
    response = await client.get(
        f"{API}/chat/threads/{thread}/pending-interrupt", headers=ann.headers
    )
    assert response.status_code == 204

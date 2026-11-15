"""Chat API (spec 09): ownership, permissions, SSE contract and history."""

import json
import os
import re
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

sys.path.insert(0, str(Path(__file__).parent / "agent"))

from agent_helpers import (  # noqa: E402
    Maker,
    make_runtime,
    scripted_llm,
    window_answer,
)

from app.agent.runtime import AgentRuntime  # noqa: E402
from app.agent.sse import EVENT_TYPES, format_event  # noqa: E402
from app.agent.state import Intent  # noqa: E402
from app.core.permissions import RoleName  # noqa: E402
from app.main import app  # noqa: E402
from app.rag.ingest import IngestReport  # noqa: E402
from conftest import API, MakeUser  # noqa: E402

QUESTION = "¿Cuántos días tengo para pedir un reembolso?"


@pytest.fixture
def runtime(session_maker: Maker) -> AsyncIterator[AgentRuntime]:
    rt = make_runtime(
        session_maker,
        scripted_llm(
            [Intent.POLICY_QUESTION, Intent.POLICY_QUESTION],
            answers=[window_answer(), window_answer()],
        ),
    )
    app.state.agent_runtime = rt
    yield rt  # type: ignore[misc]
    app.state.agent_runtime = None


def parse_sse(body: str) -> list[dict[str, Any]]:
    frames = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        frames.append(
            {
                "id": int(fields["id"]),
                "event": fields["event"],
                "data": json.loads(fields["data"]),
            }
        )
    return frames


async def create_thread(client: AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(f"{API}/chat/threads", headers=headers)
    assert response.status_code == 201
    return str(response.json()["id"])


async def test_requires_authentication(
    client: AsyncClient, runtime: AgentRuntime
) -> None:
    assert (await client.post(f"{API}/chat/threads")).status_code == 401


async def test_supervisor_cannot_use_chat(
    client: AsyncClient, make_user: MakeUser, runtime: AgentRuntime
) -> None:
    sup = await make_user(RoleName.SUPERVISOR)
    ann = await make_user()
    thread = await create_thread(client, ann.headers)
    for method, path in (
        ("POST", "/chat/threads"),
        ("GET", "/chat/threads"),
        ("GET", f"/chat/threads/{thread}/messages"),
        ("POST", f"/chat/threads/{thread}/messages"),
    ):
        response = await client.request(
            method, API + path, headers=sup.headers, json={"content": "hola"}
        )
        assert response.status_code == 403, (method, path)


async def test_threads_are_listed_per_owner(
    client: AsyncClient, make_user: MakeUser, runtime: AgentRuntime
) -> None:
    ann, bob = await make_user(), await make_user()
    mine = await create_thread(client, ann.headers)
    await create_thread(client, bob.headers)
    listing = await client.get(f"{API}/chat/threads", headers=ann.headers)
    assert [t["id"] for t in listing.json()] == [mine]
    assert listing.json()[0]["status"] == "ACTIVE"


async def test_foreign_thread_is_404(
    client: AsyncClient, make_user: MakeUser, runtime: AgentRuntime
) -> None:
    ann, bob = await make_user(), await make_user()
    theirs = await create_thread(client, bob.headers)
    read = await client.get(
        f"{API}/chat/threads/{theirs}/messages", headers=ann.headers
    )
    send = await client.post(
        f"{API}/chat/threads/{theirs}/messages",
        headers=ann.headers,
        json={"content": "hola"},
    )
    assert read.status_code == 404 and send.status_code == 404
    missing = await client.get(
        f"{API}/chat/threads/00000000-0000-0000-0000-000000000000/messages",
        headers=ann.headers,
    )
    assert missing.status_code == 404


async def test_message_validation(
    client: AsyncClient, make_user: MakeUser, runtime: AgentRuntime
) -> None:
    ann = await make_user()
    thread = await create_thread(client, ann.headers)
    url = f"{API}/chat/threads/{thread}/messages"
    for body in ({"content": ""}, {"content": "x" * 4001}, {"content": "a", "x": 1}):
        response = await client.post(url, headers=ann.headers, json=body)
        assert response.status_code == 422


async def test_sse_stream_contract_and_history(
    client: AsyncClient,
    make_user: MakeUser,
    runtime: AgentRuntime,
    ingested: IngestReport,
) -> None:
    ann = await make_user()
    thread = await create_thread(client, ann.headers)
    url = f"{API}/chat/threads/{thread}/messages"

    response = await client.post(url, headers=ann.headers, json={"content": QUESTION})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    frames = parse_sse(response.text)
    assert [f["id"] for f in frames] == list(range(1, len(frames) + 1))
    assert {f["event"] for f in frames} <= set(EVENT_TYPES)
    assert frames[-1]["event"] == "final"
    assert [f["event"] for f in frames[:-1]] == ["token"] * (len(frames) - 1)
    final = frames[-1]["data"]
    assert set(final) == {"content", "citations"} and final["citations"]

    history = await client.get(
        f"{API}/chat/threads/{thread}/messages", headers=ann.headers
    )
    assert history.status_code == 200
    assert [m["role"] for m in history.json()] == ["user", "assistant"]
    assert history.json()[0]["content"] == QUESTION
    assert history.json()[1]["content"] == final["content"]


async def test_agent_failure_becomes_an_error_event_without_internals(
    client: AsyncClient,
    make_user: MakeUser,
    session_maker: Maker,
    ingested: IngestReport,
) -> None:
    llm = scripted_llm([Intent.POLICY_QUESTION], answers=[])
    llm.structured["PolicyAnswer"] = [RuntimeError("secret internals: db password")]
    app.state.agent_runtime = make_runtime(session_maker, llm)
    try:
        ann = await make_user()
        thread = await create_thread(client, ann.headers)
        response = await client.post(
            f"{API}/chat/threads/{thread}/messages",
            headers=ann.headers,
            json={"content": QUESTION},
        )
    finally:
        app.state.agent_runtime = None
    frames = parse_sse(response.text)
    assert frames[-1]["event"] == "error"
    assert "secret" not in response.text and "password" not in response.text


async def test_chat_is_503_without_a_runtime(
    client: AsyncClient, make_user: MakeUser
) -> None:
    app.state.agent_runtime = None
    ann = await make_user()
    thread = await create_thread(client, ann.headers)
    response = await client.get(
        f"{API}/chat/threads/{thread}/messages", headers=ann.headers
    )
    assert response.status_code == 503


def test_sse_frame_format() -> None:
    frame = format_event(3, "token", {"text": "hola ñ"})
    assert frame == 'id: 3\nevent: token\ndata: {"text": "hola ñ"}\n\n'
    assert re.fullmatch(r"id: \d+\nevent: \w+\ndata: \{.*\}\n\n", frame)


async def test_history_is_rebuilt_from_the_postgres_checkpointer(
    client: AsyncClient,
    make_user: MakeUser,
    session_maker: Maker,
    ingested: IngestReport,
) -> None:
    """A brand-new graph instance (e.g. after a restart) reads the stored thread."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    from app.agent.deps import AgentDeps
    from app.agent.graph import build_graph
    from app.rag.embeddings import FakeEmbeddings
    from app.rag.retriever import PolicyRetriever

    conninfo = os.environ["DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://"
    )
    async with AsyncConnectionPool(
        conninfo,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        await saver.setup()

        def build(llm: Any) -> AgentRuntime:
            deps = AgentDeps(
                session_maker,
                llm,
                lambda s: PolicyRetriever(s, FakeEmbeddings(), threshold=0.2),
                llm_timeout_s=5,
            )
            return AgentRuntime(build_graph(deps, saver), session_maker)

        app.state.agent_runtime = build(
            scripted_llm(Intent.POLICY_QUESTION, answers=[window_answer()])
        )
        try:
            ann = await make_user()
            thread = await create_thread(client, ann.headers)
            await client.post(
                f"{API}/chat/threads/{thread}/messages",
                headers=ann.headers,
                json={"content": QUESTION},
            )
            app.state.agent_runtime = build(scripted_llm(Intent.OUT_OF_SCOPE))
            history = await client.get(
                f"{API}/chat/threads/{thread}/messages", headers=ann.headers
            )
        finally:
            app.state.agent_runtime = None
            await _drop_saver_tables(session_maker)

    assert [m["role"] for m in history.json()] == ["user", "assistant"]


async def _drop_saver_tables(maker: Maker) -> None:
    from sqlalchemy import text

    async with maker() as session:
        for table in (
            "checkpoint_writes",
            "checkpoint_blobs",
            "checkpoints",
            "checkpoint_migrations",
        ):
            await session.execute(text(f"DROP TABLE IF EXISTS {table}"))
        await session.commit()

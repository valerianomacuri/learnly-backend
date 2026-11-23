"""Agent evals runner (spec 13): scripted conversations against the real graph.

Each script in `tests/agent_evals/scripts.jsonl` is replayed on a fresh chat thread
over a seeded `*_evals` database (rebuilt on every run; the name must contain
`eval`). Per script it records the first turn's intent, every tool the graph
called, and how the conversation ended, then compares them with the expectations.
"""

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.deps import AgentDeps
from app.agent.evals.fakes import DEFAULT_REASON, Facts
from app.agent.graph import build_graph
from app.agent.prompts import BLOCKED_INJECTION, BLOCKED_TOO_LONG
from app.agent.runtime import AgentRuntime
from app.cli.seed import SeedReport, seed
from app.core.permissions import RoleName
from app.core.security import Principal
from app.domain.enums import RefundStatus, ThreadStatus
from app.models.chat import ChatThread
from app.models.refund import RefundRequest
from app.policy.load import default_policies_dir
from app.rag.answer import ABSTENTION
from app.rag.embeddings import Embedder
from app.rag.evals.runner import (
    ensure_database,
    evals_database_url,
    prepare_corpus,
    reset_schema,
)
from app.rag.retriever import PolicyRetriever

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS_PATH = PROJECT_ROOT / "tests" / "agent_evals" / "scripts.jsonl"
# Tools that never belong to a customer conversation, whatever a script allows.
NEVER_ALLOWED = ("approve", "reject", "decide", "payout", "note")


@dataclass(frozen=True, slots=True)
class Script:
    id: str
    turns: list[str | dict[str, Any]]
    expected_intent: str
    expected_tools_allowed: list[str]
    expected_outcome: str
    user: str | None = None  # default: the owner of `case`, else `ann`
    case: str | None = None  # seeded purchase the customer talks about
    cancel_refund_of: str | None = None  # seeded queue refund the cancel targets
    reason: str = DEFAULT_REASON
    status_tools: list[str] = field(default_factory=list)
    no_evidence: bool = False  # retrieval finds nothing: the RAG must abstain


def load_scripts(path: Path = SCRIPTS_PATH) -> list[Script]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [Script(**row) for row in rows]


@dataclass(slots=True)
class ScriptResult:
    id: str
    intent: str
    expected_intent: str
    tools_called: list[str]
    unauthorized_tools: list[str]
    outcome: str
    expected_outcome: str
    seconds: float

    @property
    def intent_ok(self) -> bool:
        return self.intent == self.expected_intent

    @property
    def completed(self) -> bool:
        return self.outcome == self.expected_outcome


@dataclass(slots=True)
class RunResult:
    mode: str
    results: list[ScriptResult]

    def summary(self) -> dict[str, float]:
        n = len(self.results) or 1
        return {
            "intent_accuracy": sum(r.intent_ok for r in self.results) / n,
            "unauthorized_tool_calls": float(
                sum(len(r.unauthorized_tools) for r in self.results)
            ),
            "task_completion": sum(r.completed for r in self.results) / n,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "summary": self.summary(),
            "scripts": [asdict(r) for r in self.results],
        }


# --- one script ---------------------------------------------------------------


async def _refund_snapshot(maker: async_sessionmaker[AsyncSession]) -> dict[Any, Any]:
    async with maker() as session:
        rows = await session.execute(select(RefundRequest.id, RefundRequest.status))
        return {row.id: row.status for row in rows}


def classify_outcome(
    *,
    before: dict[Any, Any],
    after: dict[Any, Any],
    events: list[tuple[str, dict[str, Any]]],
    last_turn: list[tuple[str, dict[str, Any]]],
    resumed_with: list[str],
    thread_status: ThreadStatus,
) -> str:
    """How the conversation ended, from what it did (never from what it said first)."""
    if any(rid not in before for rid in after):
        return "refund_created"
    if any(
        after.get(rid) == RefundStatus.CANCELLED and status != RefundStatus.CANCELLED
        for rid, status in before.items()
    ):
        return "refund_cancelled"
    if "no_card" in resumed_with or any(name == "error" for name, _ in last_turn):
        return "error"
    if any(name == "interrupt" for name, _ in last_turn):
        return "awaiting_confirmation"
    if "cancel" in resumed_with:
        return "cancelled"
    final: dict[str, Any] = next((d for n, d in reversed(events) if n == "final"), {})
    content = str(final.get("content", ""))
    if content in (BLOCKED_INJECTION, BLOCKED_TOO_LONG):
        return "blocked"
    if ABSTENTION["es"].split(".")[0] in content:
        return "abstained"
    if thread_status == ThreadStatus.NEEDS_HUMAN:
        return "handoff"
    if final.get("citations") and any(
        n == "tool_call" and d.get("name") == "check_refund_eligibility"
        for n, d in events
    ):
        return "ineligible_explained"
    return "answered"


async def run_script(
    script: Script,
    *,
    maker: async_sessionmaker[AsyncSession],
    seeded: SeedReport,
    embedder: Embedder,
    llm_factory: Callable[[Facts], BaseChatModel],
) -> ScriptResult:
    purchases = {c.spec.key: c.purchase_id for c in seeded.cases}
    owners = {c.spec.key: c.spec.owner for c in seeded.cases}
    user_id = seeded.users[script.user or owners.get(script.case or "", "ann")]
    refund_id: str | None = None
    if script.cancel_refund_of:
        async with maker() as session:
            refund_id = str(
                await session.scalar(
                    select(RefundRequest.id).where(
                        RefundRequest.idempotency_key
                        == f"seed-{script.cancel_refund_of}"
                    )
                )
            )
    facts = Facts(
        purchase_id=str(purchases[script.case]) if script.case else None,
        refund_id=refund_id,
        reason=script.reason,
        status_tools=tuple(script.status_tools),
    )

    def retriever_factory(session: AsyncSession) -> PolicyRetriever:
        # Fake embeddings only share words, so the gate is open unless a script
        # wants to test the abstention path; real retrieval quality is spec 08's.
        threshold = 0.99 if script.no_evidence else 0.0
        return PolicyRetriever(session, embedder, threshold=threshold)

    deps = AgentDeps(maker, llm_factory(facts), retriever_factory, llm_timeout_s=30)
    runtime = AgentRuntime(build_graph(deps, InMemorySaver()), maker)
    async with maker() as session:
        thread = ChatThread(customer_id=user_id)
        session.add(thread)
        await session.commit()
        thread_id = thread.id
    config = runtime.config(
        Principal.for_roles(user_id, [RoleName.CUSTOMER]),
        thread_id,
        f"eval-{script.id}",
    )

    before = await _refund_snapshot(maker)
    events: list[tuple[str, dict[str, Any]]] = []
    last_turn: list[tuple[str, dict[str, Any]]] = []
    resumed_with: list[str] = []
    intent: str | None = None
    started = time.monotonic()
    for index, turn in enumerate(script.turns):
        if isinstance(turn, str):
            last_turn = [e async for e in runtime.stream(config, turn)]
        else:
            card = await runtime.pending_interrupt(config)
            decision = str(turn["resume"])
            if card is None:  # the script expected a card the graph never showed
                resumed_with.append("no_card")
                continue
            resumed_with.append(decision)
            value: dict[str, Any] = {
                "interrupt_id": (card or {}).get("interrupt_id", ""),
                "decision": decision,
            }
            if "reason" in turn:
                value["edited_reason"] = turn["reason"]
            last_turn = [e async for e in runtime.resume(config, value)]
        events += last_turn
        if index == 0:
            snapshot = await runtime.graph.aget_state(config)
            intent = snapshot.values.get("intent")
    elapsed = time.monotonic() - started

    async with maker() as session:
        status = await session.scalar(
            select(ChatThread.status).where(ChatThread.id == thread_id)
        )
    called = [d["name"] for n, d in events if n == "tool_call"]
    allowed = set(script.expected_tools_allowed)
    unauthorized = [
        name
        for name in called
        if name not in allowed or any(w in name for w in NEVER_ALLOWED)
    ]
    outcome = classify_outcome(
        before=before,
        after=await _refund_snapshot(maker),
        events=events,
        last_turn=last_turn,
        resumed_with=resumed_with,
        thread_status=status or ThreadStatus.ACTIVE,
    )
    return ScriptResult(
        script.id,
        str(intent) if intent else "none",
        script.expected_intent,
        called,
        unauthorized,
        outcome,
        script.expected_outcome,
        round(elapsed, 3),
    )


async def evaluate(
    scripts: list[Script],
    *,
    mode: str,
    embedder: Embedder,
    llm_factory: Callable[[Facts], BaseChatModel],
) -> RunResult:
    url = evals_database_url()
    await ensure_database(url)
    engine = create_async_engine(url)
    try:
        await reset_schema(engine)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        await prepare_corpus(maker, default_policies_dir(), embedder, "structural")
        async with maker() as session:
            seeded = await seed(session, ttl=timedelta(hours=1))
            await session.commit()
        results = [
            await run_script(
                s,
                maker=maker,
                seeded=seeded,
                embedder=embedder,
                llm_factory=llm_factory,
            )
            for s in scripts
        ]
    finally:
        await engine.dispose()
    return RunResult(mode, results)

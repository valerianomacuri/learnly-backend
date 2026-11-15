from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.rag.answer import (
    ABSTENTION,
    GroundingVerdict,
    PolicyAnswer,
    PolicyAnswerer,
    unverified_numbers,
)
from app.rag.embeddings import FakeEmbeddings
from app.rag.ingest import IngestReport
from app.rag.llm import FakeChatModel
from app.rag.retriever import PolicyRetriever
from app.repositories.policy import PolicyRepository
from app.schemas.refund import Citation

Maker = async_sessionmaker[AsyncSession]
WINDOW = Citation(doc_key="refund-eligibility", section_path="Ventana de 30 días")


def scripted(
    answers: list[PolicyAnswer], supported: bool = True
) -> tuple[FakeChatModel, list[str]]:
    seen: list[str] = []
    queue = list(answers)

    def handler(system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        if schema is GroundingVerdict:
            return GroundingVerdict(supported=supported)
        seen.append(user)
        return queue.pop(0)

    return FakeChatModel(structured=handler), seen


async def ask(
    maker: Maker, llm: FakeChatModel, question: str, threshold: float = 0.2
) -> tuple[PolicyAnswer, list[str]]:
    async with maker() as session:
        config = await PolicyRepository(session).get_active_config()
        retriever = PolicyRetriever(session, FakeEmbeddings(), threshold=threshold)
        result = await PolicyAnswerer(retriever, llm, config).answer(question)
    return result.answer, result.trace


async def test_grounded_answer_with_valid_qualified_citation(
    session_maker: Maker, ingested: IngestReport
) -> None:
    llm, _ = scripted(
        [
            PolicyAnswer(
                answer="Tienes 30 días.", citations=[WINDOW], needs_handoff=False
            )
        ]
    )
    answer, trace = await ask(
        session_maker, llm, "¿Cuántos días tengo para pedir un reembolso?"
    )
    assert answer.answer == "Tienes 30 días." and not answer.needs_handoff
    assert [c.section_path for c in answer.citations] == [
        "Elegibilidad > Ventana de 30 días"
    ]
    assert trace[-1] == "verified"


async def test_wrong_number_is_corrected_with_one_regeneration(
    session_maker: Maker, ingested: IngestReport
) -> None:
    llm, prompts = scripted(
        [
            PolicyAnswer(
                answer="Sí, son 60 días.", citations=[WINDOW], needs_handoff=False
            ),
            PolicyAnswer(
                answer="No: son 30 días.", citations=[WINDOW], needs_handoff=False
            ),
        ]
    )
    answer, trace = await ask(session_maker, llm, "son 60 días, no? reembolso días")
    assert answer.answer == "No: son 30 días."
    assert len(prompts) == 2 and "60" in prompts[1]
    assert any("unsupported ['60']" in line for line in trace)


async def test_persistent_wrong_number_abstains(
    session_maker: Maker, ingested: IngestReport
) -> None:
    bad = PolicyAnswer(answer="Son 60 días.", citations=[WINDOW], needs_handoff=False)
    llm, _ = scripted([bad, bad])
    answer, _ = await ask(session_maker, llm, "son 60 días, no? reembolso días")
    assert answer.needs_handoff and answer.citations == []
    assert answer.answer == ABSTENTION["es"]


async def test_out_of_scope_abstains_without_calling_the_llm(
    session_maker: Maker, ingested: IngestReport
) -> None:
    llm, _ = scripted([])
    answer, trace = await ask(
        session_maker, llm, "quark gluon boson hadron", threshold=0.3
    )
    assert answer.needs_handoff and llm.calls == []
    assert "abstain: insufficient evidence" in trace


async def test_invalid_citation_is_dropped_and_none_left_abstains(
    session_maker: Maker, ingested: IngestReport
) -> None:
    fake = Citation(doc_key="refund-eligibility", section_path="No existe")
    llm, _ = scripted(
        [PolicyAnswer(answer="Tienes 30 días.", citations=[fake], needs_handoff=False)]
    )
    answer, trace = await ask(
        session_maker, llm, "¿Cuántos días tengo para pedir un reembolso?"
    )
    assert answer.needs_handoff
    assert "abstain: no valid citation" in trace


async def test_ungrounded_answer_abstains(
    session_maker: Maker, ingested: IngestReport
) -> None:
    llm, _ = scripted(
        [
            PolicyAnswer(
                answer="Tienes 30 días.", citations=[WINDOW], needs_handoff=False
            )
        ],
        supported=False,
    )
    answer, trace = await ask(
        session_maker, llm, "¿Cuántos días tengo para pedir un reembolso?"
    )
    assert answer.needs_handoff and answer.citations == []
    assert "abstain: grounding check failed" in trace


def test_unverified_numbers_uses_policy_config_and_context() -> None:
    from app.domain.policy_config import PolicyConfig

    config = PolicyConfig(
        refund_window_days=30,
        max_progress_pct=30,
        abuse_flag_12m=3,
        abuse_block_12m=5,
        confirmation_ttl_min=10,
        sla_hours=48,
        one_active_request_per_purchase=True,
        rejected_is_final=True,
        policy_version="v",
        reason_code_map={},
    )
    assert unverified_numbers("30 días y 30 % de avance", config, []) == []
    assert unverified_numbers("60 días", config, []) == ["60"]
    assert unverified_numbers("30,0 días", config, []) == []

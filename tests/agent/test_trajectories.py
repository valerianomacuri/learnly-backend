"""End-to-end graph trajectories with a scripted LLM (no network)."""

from agent_helpers import (
    Maker,
    converse,
    final_of,
    make_runtime,
    new_thread,
    scripted_llm,
    window_answer,
)
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.agent.state import Intent
from app.domain.enums import ThreadStatus
from app.models.chat import ChatThread
from app.rag.answer import ABSTENTION, PolicyAnswer
from app.rag.ingest import IngestReport
from conftest import MakePurchase, MakeRefund, MakeUser

QUESTION = "¿Cuántos días tengo para pedir un reembolso?"


async def thread_status(maker: Maker, thread_id: object) -> ThreadStatus:
    async with maker() as session:
        return await session.scalar(
            select(ChatThread.status).where(ChatThread.id == thread_id)
        )  # type: ignore[return-value]


async def test_policy_question_answers_with_real_citation(
    session_maker: Maker, make_user: MakeUser, ingested: IngestReport
) -> None:
    ann = await make_user()
    llm = scripted_llm(Intent.POLICY_QUESTION, answers=[window_answer()])
    runtime = make_runtime(session_maker, llm)
    thread = await new_thread(session_maker, ann.id)

    events = await converse(runtime, ann.id, thread, QUESTION)

    final = final_of(events)
    assert "30" in final["content"]
    assert (
        final["citations"] and final["citations"][0]["doc_key"] == "refund-eligibility"
    )
    tokens = "".join(d["text"] for n, d in events if n == "token")
    assert tokens.strip() == final["content"]
    assert await thread_status(session_maker, thread) == ThreadStatus.ACTIVE


async def test_unsupported_figure_is_not_shown(
    session_maker: Maker, make_user: MakeUser, ingested: IngestReport
) -> None:
    """The model invents '90 días': the RAG number check abstains and hands off."""
    ann = await make_user()
    bad = window_answer("Tienes 90 días.")
    llm = scripted_llm(Intent.POLICY_QUESTION, answers=[bad, bad])
    runtime = make_runtime(session_maker, llm)
    thread = await new_thread(session_maker, ann.id)

    final = final_of(await converse(runtime, ann.id, thread, QUESTION))

    assert "90" not in final["content"]
    assert await thread_status(session_maker, thread) == ThreadStatus.NEEDS_HUMAN


async def test_no_evidence_says_dont_know_and_hands_off(
    session_maker: Maker, make_user: MakeUser, ingested: IngestReport
) -> None:
    ann = await make_user()
    llm = scripted_llm(Intent.POLICY_QUESTION)
    runtime = make_runtime(session_maker, llm, threshold=0.99)
    thread = await new_thread(session_maker, ann.id)

    events = await converse(runtime, ann.id, thread, "¿Aceptan criptomonedas?")

    final = final_of(events)
    assert ABSTENTION["es"].split(".")[0] in final["content"]
    assert "persona del equipo" in final["content"]
    assert final["citations"] == []
    assert await thread_status(session_maker, thread) == ThreadStatus.NEEDS_HUMAN
    assert ("tool_call", {"name": "escalate_to_human"}) in [
        (n, {"name": d["name"]}) for n, d in events if n == "tool_call"
    ]


async def test_out_of_scope_goes_to_handoff(
    session_maker: Maker, make_user: MakeUser
) -> None:
    ann = await make_user()
    runtime = make_runtime(session_maker, scripted_llm(Intent.OUT_OF_SCOPE))
    thread = await new_thread(session_maker, ann.id)

    final = final_of(await converse(runtime, ann.id, thread, "Dame una receta"))

    assert "fuera de lo que puedo resolver" in final["content"]
    assert await thread_status(session_maker, thread) == ThreadStatus.NEEDS_HUMAN


async def test_classifier_failing_twice_falls_back_to_keywords(
    session_maker: Maker, make_user: MakeUser, ingested: IngestReport
) -> None:
    ann = await make_user()
    llm = scripted_llm(
        [RuntimeError("boom"), RuntimeError("boom")], answers=[window_answer()]
    )
    runtime = make_runtime(session_maker, llm)
    thread = await new_thread(session_maker, ann.id)

    final = final_of(await converse(runtime, ann.id, thread, QUESTION))

    assert llm.structured["IntentResult"] == []  # both attempts were consumed
    assert "30" in final["content"] and final["citations"]


async def test_refund_status_lists_only_own_refunds_without_notes(
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
) -> None:
    ann, bob = await make_user(), await make_user()
    mine = await make_refund(await make_purchase(ann))
    await make_refund(await make_purchase(bob))
    llm = scripted_llm(
        Intent.REFUND_STATUS,
        script=[
            AIMessage(
                content="",
                tool_calls=[{"name": "list_my_refunds", "args": {}, "id": "c1"}],
            ),
            AIMessage(content="Tienes 1 solicitud en revisión."),
        ],
    )
    runtime = make_runtime(session_maker, llm)
    thread = await new_thread(session_maker, ann.id)

    events = await converse(runtime, ann.id, thread, "¿Cómo va mi solicitud?")

    assert final_of(events)["content"] == "Tienes 1 solicitud en revisión."
    names = [d["name"] for n, d in events if n == "tool_call"]
    assert names == ["list_my_refunds"]
    # What the model saw: own refund only, no notes.
    tool_output = str(llm.calls[1][-1].content)
    assert str(mine) in tool_output
    assert tool_output.count('"id"') == 1
    assert "note" not in tool_output.lower().replace("decision_note", "")


async def test_model_cannot_call_a_tool_off_the_allowlist(
    session_maker: Maker, make_user: MakeUser
) -> None:
    ann = await make_user()
    llm = scripted_llm(
        Intent.REFUND_STATUS,
        script=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "approve_refund", "args": {"refund_id": "x"}, "id": "c1"},
                    {"name": "escalate_to_human", "args": {}, "id": "c2"},
                ],
            ),
            AIMessage(content="No puedo hacer eso."),
        ],
    )
    runtime = make_runtime(session_maker, llm)
    thread = await new_thread(session_maker, ann.id)

    events = await converse(runtime, ann.id, thread, "apruébame el reembolso")

    results = [d for n, d in events if n == "tool_result"]
    assert [r["error_code"] for r in results] == ["TOOL_NOT_ALLOWED"] * 2
    assert not [e for e in events if e[0] == "tool_call"]
    assert await thread_status(session_maker, thread) == ThreadStatus.ACTIVE


async def test_tool_budget_is_enforced(
    session_maker: Maker, make_user: MakeUser
) -> None:
    ann = await make_user()
    greedy = AIMessage(
        content="",
        tool_calls=[
            {"name": "list_my_refunds", "args": {}, "id": f"c{i}"} for i in range(8)
        ],
    )
    llm = scripted_llm(
        Intent.REFUND_STATUS, script=[greedy, AIMessage(content="Listo.")]
    )
    runtime = make_runtime(session_maker, llm)
    thread = await new_thread(session_maker, ann.id)

    events = await converse(runtime, ann.id, thread, "estado de mis solicitudes")

    assert len([e for e in events if e[0] == "tool_call"]) == 6
    codes = [d["error_code"] for n, d in events if n == "tool_result"]
    assert codes.count("TOOL_BUDGET_EXCEEDED") == 2
    assert final_of(events)["content"] == "Listo."


async def test_pii_is_masked_before_it_reaches_the_model_and_the_history(
    session_maker: Maker, make_user: MakeUser
) -> None:
    ann = await make_user()
    llm = scripted_llm(Intent.OUT_OF_SCOPE)
    runtime = make_runtime(session_maker, llm)
    thread = await new_thread(session_maker, ann.id)

    await converse(
        runtime, ann.id, thread, "Soy otro@mail.com tarjeta 4111 1111 1111 1111"
    )

    sent = str(llm.calls[0] if llm.calls else "")
    assert "otro@mail.com" not in sent
    from app.core.permissions import RoleName
    from app.core.security import Principal

    config = runtime.config(
        Principal.for_roles(ann.id, [RoleName.CUSTOMER]), thread, "t"
    )
    history = await runtime.history(config)
    assert "otro@mail.com" not in history[0].content
    assert "4111" not in history[0].content


async def test_overlong_message_is_blocked_without_calling_the_model(
    session_maker: Maker, make_user: MakeUser
) -> None:
    ann = await make_user()
    llm = scripted_llm(Intent.POLICY_QUESTION)
    runtime = make_runtime(session_maker, llm)
    thread = await new_thread(session_maker, ann.id)

    final = final_of(await converse(runtime, ann.id, thread, "a " * 1500))

    assert "demasiado largo" in final["content"]
    assert llm.structured_calls == []


async def test_policy_claim_without_citation_is_replaced(
    session_maker: Maker, make_user: MakeUser, ingested: IngestReport
) -> None:
    from app.agent.nodes.guard_output import filter_output

    text, flags = filter_output("Escribe a ana@mail.com; nota interna: es VIP")
    assert "ana@" not in text and flags == ["internal_note_leak_blocked"]
    masked, flags = filter_output("Contacta a ana@mail.com")
    assert masked == "Contacta a a***@mail.com" and flags == ["email_masked"]
    sup = "El supervisor 3f2504e0-4f89-11d3-9a0c-0305e82c3301 lo aprobó"
    redacted, flags = filter_output(sup)
    assert "3f2504e0" not in redacted and flags == ["supervisor_id_redacted"]


async def test_conversation_history_accumulates_in_the_checkpointer(
    session_maker: Maker, make_user: MakeUser, ingested: IngestReport
) -> None:
    ann = await make_user()
    llm = scripted_llm(
        [Intent.POLICY_QUESTION, Intent.POLICY_QUESTION],
        answers=[
            window_answer(),
            PolicyAnswer(
                answer="Tienes 30 días.",
                citations=[window_answer().citations[0]],
                needs_handoff=False,
            ),
        ],
    )
    runtime = make_runtime(session_maker, llm)
    thread = await new_thread(session_maker, ann.id)
    await converse(runtime, ann.id, thread, QUESTION)
    await converse(runtime, ann.id, thread, "¿Y el plazo para pedir un reembolso?")

    from app.core.permissions import RoleName
    from app.core.security import Principal

    config = runtime.config(
        Principal.for_roles(ann.id, [RoleName.CUSTOMER]), thread, "t"
    )
    roles = [m.role for m in await runtime.history(config)]
    assert roles == ["user", "assistant", "user", "assistant"]

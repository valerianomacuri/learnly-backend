"""Privacy (spec 12): emails never reach the LLM or the logs; notes never reach the LLM."""

import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

sys.path.insert(0, str(Path(__file__).parent.parent / "agent"))

from agent_helpers import (  # noqa: E402
    Maker,
    converse,
    make_runtime,
    new_thread,
    scripted_llm,
    window_answer,
)

from app.agent.state import Intent  # noqa: E402
from app.core.permissions import RoleName  # noqa: E402
from app.core.privacy import RedactingFilter  # noqa: E402
from app.models.refund import RefundInternalNote  # noqa: E402
from conftest import MakePurchase, MakeRefund, MakeUser  # noqa: E402

NOTE = "Cliente con historial de contracargos, vigilar"


def dump(messages: Any) -> str:
    return json.dumps([str(getattr(m, "content", m)) for m in messages])


async def test_email_and_notes_stay_out_of_prompts_and_logs(
    session_maker: Maker,
    make_user: MakeUser,
    make_purchase: MakePurchase,
    make_refund: MakeRefund,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ann = await make_user(RoleName.CUSTOMER)
    sup = await make_user(RoleName.SUPERVISOR)
    refund = await make_refund(await make_purchase(ann, days_ago=5, progress=2))
    async with session_maker() as session:
        session.add(
            RefundInternalNote(refund_id=refund, supervisor_id=sup.id, body=NOTE)
        )
        await session.commit()

    seen: list[str] = []
    llm = scripted_llm(
        [Intent.POLICY_QUESTION, Intent.REFUND_STATUS],
        answers=[window_answer()],
    )
    original = llm.structured

    def recording(name: str) -> Any:
        handler = original[name]

        def run(messages: Any) -> Any:
            seen.append(dump(messages))
            if isinstance(handler, list):
                return handler.pop(0)
            return handler

        return run

    llm.structured = {name: recording(name) for name in original}
    runtime = make_runtime(session_maker, llm)
    thread = await new_thread(session_maker, ann.id)

    caplog.set_level(logging.DEBUG)
    caplog.handler.addFilter(RedactingFilter())
    email = "mi.correo.privado@example.com"
    await converse(
        runtime,
        ann.id,
        thread,
        f"Mi email es {email} y mi tarjeta 4111 1111 1111 1111"
        ", ¿cuántos días tengo para pedir un reembolso?",
    )
    llm.script.append(AIMessage(content="Tu solicitud está pendiente."))
    await converse(
        runtime, ann.id, thread, f"¿Cómo va mi solicitud? Escríbeme a {email}"
    )

    prompts = " ".join(seen) + " " + " ".join(dump(c) for c in llm.calls)
    assert prompts.strip(), "the LLM was never called"
    for secret in (email, "4111 1111 1111 1111", NOTE):
        assert secret not in prompts, f"{secret!r} reached the LLM"
    assert email not in caplog.text and NOTE not in caplog.text

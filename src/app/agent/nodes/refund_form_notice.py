"""Provisional route for `refund_request` / `cancel_refund` until spec 10."""

from typing import Any

from app.agent.prompts import FORM_NOTICE
from app.agent.state import SupportState


async def refund_form_notice(state: SupportState) -> dict[str, Any]:
    return {"draft": FORM_NOTICE}

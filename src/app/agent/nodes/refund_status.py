"""`refund_status`: bounded ReAct sub-agent over the read tools (budget 6)."""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.agent.deps import AgentDeps, last_user_text
from app.agent.prompts import STATUS_FALLBACK, STATUS_SYSTEM
from app.agent.state import TOOL_BUDGET, SupportState
from app.agent.tools.base import ToolResult, run_tool
from app.agent.tools.registry import ALL_TOOLS, LLM_BOUND_TOOLS, bound_specs


def _text(message: AIMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        part if isinstance(part, str) else str(part.get("text", "")) for part in content
    )


def make_refund_status(
    deps: AgentDeps,
) -> Callable[[SupportState, RunnableConfig], Awaitable[dict[str, Any]]]:
    async def refund_status(
        state: SupportState, config: RunnableConfig
    ) -> dict[str, Any]:
        ctx = deps.tool_context(config)
        emit = get_stream_writer()
        budget = int(state.get("tool_budget", TOOL_BUDGET))
        with_tools = deps.llm.bind_tools([s.openai_schema() for s in bound_specs()])
        question = last_user_text(state.get("messages", []))
        convo: list[BaseMessage] = [
            SystemMessage(STATUS_SYSTEM),
            HumanMessage(f"<customer_message>\n{question}\n</customer_message>"),
        ]
        # Each round either answers or spends tool budget; the last one has no tools.
        for _ in range(TOOL_BUDGET + 2):
            model = with_tools if budget > 0 else deps.llm
            reply = await deps.timed(model.ainvoke(convo))
            if not isinstance(reply, AIMessage) or not reply.tool_calls:
                text = _text(reply) if isinstance(reply, AIMessage) else ""
                return {"draft": text or STATUS_FALLBACK, "tool_budget": budget}
            convo.append(reply)
            for call in reply.tool_calls:
                name = call["name"]
                if name not in LLM_BOUND_TOOLS:
                    # Off-allowlist names are refused even if the model invents them.
                    result = ToolResult.failure(
                        "TOOL_NOT_ALLOWED", "Esa herramienta no está disponible."
                    )
                elif budget <= 0:
                    result = ToolResult.failure(
                        "TOOL_BUDGET_EXCEEDED", "Se agotó el presupuesto de consultas."
                    )
                else:
                    budget -= 1
                    emit({"type": "tool_call", "name": name, "args": call["args"]})
                    result = await run_tool(ALL_TOOLS[name], ctx, dict(call["args"]))
                emit(
                    {
                        "type": "tool_result",
                        "name": name,
                        "ok": result.ok,
                        "error_code": result.error.code if result.error else None,
                    }
                )
                convo.append(
                    ToolMessage(
                        content=json.dumps(result.model_dump(), default=str),
                        tool_call_id=call["id"] or name,
                    )
                )
        return {"draft": STATUS_FALLBACK, "tool_budget": budget}

    return refund_status

"""Opt-in LangSmith traces of the graph, per node (spec 13).

`TRACING_PROVIDER=none` (default) builds nothing: no client, no network. With
`langsmith` an explicit `LangChainTracer` is attached to the run config by
`AgentRuntime._run`; the global `LANGSMITH_TRACING` mode is deliberately not used
(it would also trace RAG and the evals and would bypass the scrubbing below).

Privacy (spec 12): the client hides inputs/outputs through `scrub`, which masks
PII and drops confirmation tokens, payload hashes and internal notes. Fail-open:
any error building or using the tracer is logged and the request goes on.
"""

import logging
import re
from typing import Any

from langchain_core.tracers.langchain import LangChainTracer

from app.agent.guardrails.pii import mask_pii
from app.core.config import Settings

logger = logging.getLogger("app.agent.tracing")

_DROP_KEYS = re.compile(r"token|hash|secret|password|authorization|note|api_key", re.I)
_REDACTED = "[redacted]"


def scrub(value: Any) -> Any:
    """Recursively mask PII in strings and drop sensitive keys."""
    if isinstance(value, str):
        return mask_pii(value)[0]
    if isinstance(value, dict):
        return {
            k: (_REDACTED if isinstance(k, str) and _DROP_KEYS.search(k) else scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [scrub(v) for v in value]
    return value


def safe_scrub(value: dict[str, Any]) -> dict[str, Any]:
    """`scrub` for LangSmith's hide hooks: on failure send nothing, never raw data."""
    try:
        result = scrub(value)
        return result if isinstance(result, dict) else {}
    except Exception:
        logger.exception("trace scrubbing failed; dropping the payload")
        return {}


def build_tracer(settings: Settings) -> LangChainTracer | None:
    """The tracer for this process, or `None` when tracing is off or broken."""
    if settings.tracing_provider != "langsmith" or settings.langsmith_api_key is None:
        return None
    try:
        from langsmith import Client

        client = Client(
            api_url=settings.langsmith_endpoint,
            api_key=settings.langsmith_api_key.get_secret_value(),
            hide_inputs=safe_scrub,
            hide_outputs=safe_scrub,
        )
        return LangChainTracer(client=client, project_name=settings.langsmith_project)
    except Exception:
        logger.exception("LangSmith tracer unavailable; tracing disabled")
        return None


def run_metadata(config: dict[str, Any]) -> dict[str, str]:
    """Ids only; `request_id` equals `refund_events.payload.trace_id`."""
    conf = config.get("configurable", {})
    return {
        key: str(conf[key])
        for key in ("request_id", "thread_id", "user_id")
        if conf.get(key)
    }

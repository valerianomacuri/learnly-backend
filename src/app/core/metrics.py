"""Prometheus business metrics (spec 13).

Module-level collectors on the default registry, incremented where the event
happens. Gauges that mirror database state (SLA breaches, outbox backlog) are
refreshed at scrape time by `api/routes/ops.py`.
"""

from prometheus_client import Counter, Gauge, Histogram

REFUND_REQUESTS = Counter(
    "refund_requests_total", "Refund requests created", ["channel"]
)
ELIGIBILITY_REJECTIONS = Counter(
    "refund_eligibility_rejections_total",
    "Refund attempts rejected by the policy engine",
    ["reason_code"],
)
CONFIRMATIONS = Counter(
    "refund_confirmations_total",
    "Customer answers to the HITL confirmation card",
    ["decision"],
)
DECISIONS = Counter("refund_decisions_total", "Supervisor decisions", ["decision"])
TIME_TO_DECISION = Histogram(
    "refund_time_to_decision_seconds",
    "Seconds from request creation to supervisor decision",
    buckets=(60, 300, 900, 3600, 4 * 3600, 12 * 3600, 24 * 3600, 48 * 3600, 96 * 3600),
)
SLA_BREACHED = Gauge("refund_sla_breached", "Pending refunds past their SLA")
OUTBOX_PENDING = Gauge("refund_outbox_pending", "Outbox events waiting to run")
OUTBOX_DEAD = Gauge("refund_outbox_dead", "Outbox events dead-lettered")
PAYOUTS_DEAD = Counter("refund_payouts_dead_total", "Payouts that ran out of retries")
LLM_LATENCY = Histogram(
    "refund_llm_latency_seconds",
    "Duration of one agent run (one chat turn)",
    buckets=(0.5, 1, 2, 4, 8, 16, 32, 64),
)
LLM_TOKENS = Counter("refund_llm_tokens_total", "LLM tokens used by the agent")
LLM_COST = Counter("refund_llm_cost_usd_total", "Estimated LLM cost in USD")

# USD per 1M tokens, blended input/output (gpt-4o-mini order of magnitude). An
# estimate for dashboards; LangSmith computes the exact cost when tracing is on.
_USD_PER_MTOKEN = 0.30


def record_llm_tokens(tokens: int) -> None:
    if tokens > 0:
        LLM_TOKENS.inc(tokens)
        LLM_COST.inc(tokens * _USD_PER_MTOKEN / 1_000_000)

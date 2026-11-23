"""Token usage tracking and the per-thread budget (spec 12).

`UsageCallback` adds up the `usage_metadata` of every LLM call of a run (it is
attached through the run config, so nested node calls report to it).
`TokenBudget` keeps the running total per thread in Redis (`INCRBY`); without Redis
it falls back to a per-process counter, which is documented as a degradation.
"""

import logging
import uuid
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from redis.asyncio import Redis

logger = logging.getLogger("app.agent.budget")

BUDGET_ERROR_CODE = "TOKEN_BUDGET_EXCEEDED"
BUDGET_MESSAGE = (
    "Esta conversación alcanzó su límite de uso. Inicia una conversación nueva "
    "o usa el formulario de reembolsos."
)
_TTL_S = 7 * 24 * 3600


class UsageCallback(BaseCallbackHandler):
    """Sums `total_tokens` of every finished LLM call."""

    def __init__(self) -> None:
        self.total = 0

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        for generations in response.generations:
            for generation in generations:
                usage = getattr(
                    getattr(generation, "message", None), "usage_metadata", None
                )
                if usage:
                    self.total += int(usage.get("total_tokens", 0))


class TokenBudget:
    def __init__(self, redis: Redis | None, limit: int) -> None:
        self._redis = redis
        self.limit = limit
        self._memory: dict[str, int] = {}

    @staticmethod
    def _key(thread_id: uuid.UUID | str) -> str:
        return f"tokens:thread:{thread_id}"

    async def used(self, thread_id: uuid.UUID | str) -> int:
        key = self._key(thread_id)
        if self._redis is not None:
            try:
                return int(await self._redis.get(key) or 0)
            except Exception:
                logger.warning("token budget: redis unavailable, using memory")
        return self._memory.get(key, 0)

    async def exhausted(self, thread_id: uuid.UUID | str) -> bool:
        return await self.used(thread_id) >= self.limit

    async def add(self, thread_id: uuid.UUID | str, tokens: int) -> int:
        if tokens <= 0:
            return await self.used(thread_id)
        key = self._key(thread_id)
        if self._redis is not None:
            try:
                total = int(await self._redis.incrby(key, tokens))
                await self._redis.expire(key, _TTL_S)
                return total
            except Exception:
                logger.warning("token budget: redis unavailable, using memory")
        self._memory[key] = self._memory.get(key, 0) + tokens
        return self._memory[key]

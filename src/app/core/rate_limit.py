"""Sliding-window rate limiter over a Redis sorted set (spec 12).

The whole check-and-record is one Lua script, so concurrent requests cannot
overshoot the limit, and it uses Redis' own clock (no skew between API nodes).
"""

import logging
import math
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_SCRIPT = """
local t = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)
local window = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return {0, tonumber(oldest[2]) + window - now}
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window)
return {1, 0}
"""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    retry_after: int = 0  # whole seconds, >= 1 when blocked


class SlidingWindowLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit(self, key: str, *, limit: int, window_s: float) -> Decision:
        """Record one event for `key`; raises on Redis errors (the caller decides)."""
        window_ms = int(window_s * 1000)
        allowed, wait_ms = await self._redis.eval(
            _SCRIPT, 1, key, window_ms, limit, uuid.uuid4().hex
        )
        if allowed:
            return Decision(True)
        return Decision(False, max(1, math.ceil(int(wait_ms) / 1000)))

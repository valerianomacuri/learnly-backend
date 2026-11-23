"""Redis client and the per-thread run lock (spec 12).

`REDIS_URL` unset means "no Redis configured": rate limits and locks are off
(local development). A configured but unreachable Redis is an outage: the
limiter fails open for reads and closed for writes (`core/rate_limit.py`).
"""

import logging
import uuid

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

LOCK_TTL_MS = 120_000


def open_redis(url: str | None) -> Redis | None:
    """Lazy client: nothing connects until the first command."""
    if not url:
        return None
    return Redis.from_url(
        url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2
    )


class ThreadLock:
    """One graph run per chat thread. Best effort: a Redis error never blocks a run."""

    def __init__(self, redis: Redis | None, thread_id: uuid.UUID) -> None:
        self._redis = redis
        self._key = f"lock:thread:{thread_id}"
        self._token = uuid.uuid4().hex
        self.held = False

    async def acquire(self) -> bool:
        """False only when another run provably holds the lock."""
        if self._redis is None:
            return True
        try:
            got = await self._redis.set(self._key, self._token, nx=True, px=LOCK_TTL_MS)
        except Exception:
            logger.warning("thread lock unavailable; running without it")
            return True
        self.held = bool(got)
        return self.held

    async def release(self) -> None:
        if self._redis is None or not self.held:
            return
        self.held = False
        try:
            await self._redis.eval(_RELEASE, 1, self._key, self._token)
        except Exception:
            logger.warning("thread lock release failed; it expires by TTL")

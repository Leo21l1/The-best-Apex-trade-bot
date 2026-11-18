"""Fixed-window rate limiting backed by Redis with an in-process fallback.

If Redis is unavailable the limiter degrades to a per-process in-memory window rather than
failing open entirely.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable

from redis.exceptions import RedisError

from app.core.redis import get_redis


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        # Wall-clock source for window bucketing; injectable so tests can pin the window.
        self.clock = clock
        self._local: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))

    def _local_hit(self, key: str, window: int) -> int:
        w, count = self._local[key]
        if w != window:
            w, count = window, 0
        count += 1
        self._local[key] = (w, count)
        if len(self._local) > 50_000:  # bound memory
            self._local.clear()
        return count

    async def hit(self, bucket: str, client: str, limit: int, period_s: int = 60) -> bool:
        """Register a hit; return True if allowed."""
        window = int(self.clock()) // period_s
        key = f"rl:{bucket}:{client}:{window}"
        try:
            redis = get_redis()
            pipe = redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, period_s + 1)
            count, _ = await pipe.execute()
        except (RedisError, OSError):
            count = self._local_hit(key, window)
        return int(count) <= limit

    def reset_local(self) -> None:
        self._local.clear()


rate_limiter = RateLimiter()

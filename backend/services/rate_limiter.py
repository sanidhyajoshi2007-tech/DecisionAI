"""
In-memory rate limiter (sliding window, no Redis required).

Tracks timestamps per client IP / user identifier in memory and raises
HTTP 429 when threshold is exceeded.
"""

import asyncio
import time
from collections import defaultdict
from typing import Callable

from fastapi import HTTPException, Request, status


class InMemoryRateLimiter:
    """
    Sliding window in-memory rate limiter.
    
    Thread-safe and requires no external dependency (Redis).
    """

    def __init__(self, requests_per_minute: int = 60, window_seconds: int = 60):
        self.requests_per_minute = requests_per_minute
        self.window_seconds = window_seconds
        self._records: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._last_cleanup = time.time()

    async def _cleanup(self, now: float) -> None:
        """Evict stale keys older than window to prevent memory leaks."""
        cutoff = now - self.window_seconds
        stale_keys = []
        for key, timestamps in self._records.items():
            valid = [ts for ts in timestamps if ts > cutoff]
            if not valid:
                stale_keys.append(key)
            else:
                self._records[key] = valid

        for k in stale_keys:
            del self._records[k]

        self._last_cleanup = now

    async def check(self, key: str, max_requests: int | None = None) -> tuple[bool, int, int]:
        """
        Check whether request with key is allowed.
        Returns:
            (allowed: bool, retry_after: int, remaining: int)
        """
        limit = max_requests or self.requests_per_minute
        now = time.time()

        async with self._lock:
            # Periodic cleanup every 60 seconds
            if now - self._last_cleanup > 60:
                await self._cleanup(now)

            cutoff = now - self.window_seconds
            timestamps = [ts for ts in self._records[key] if ts > cutoff]

            if len(timestamps) >= limit:
                # Oldest timestamp in window determines when slot frees up
                oldest_in_window = timestamps[0]
                retry_after = max(1, int(oldest_in_window + self.window_seconds - now))
                return False, retry_after, 0

            timestamps.append(now)
            self._records[key] = timestamps
            remaining = limit - len(timestamps)
            return True, 0, remaining

    def reset(self) -> None:
        """Clear all rate limit records (useful in tests)."""
        self._records.clear()
        self._last_cleanup = time.time()


# Default singleton instance
default_rate_limiter = InMemoryRateLimiter(requests_per_minute=60, window_seconds=60)


def rate_limit(requests_per_minute: int = 60, window_seconds: int = 60) -> Callable:
    """
    FastAPI dependency factory for rate limiting.
    
    Usage:
        @router.get("/endpoint", dependencies=[Depends(rate_limit(60))])
    """
    limiter = InMemoryRateLimiter(requests_per_minute=requests_per_minute, window_seconds=window_seconds)

    async def dependency(request: Request) -> None:
        # Determine client identifier: X-Forwarded-For or client.host
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        elif request.client:
            client_ip = request.client.host
        else:
            client_ip = "127.0.0.1"

        key = f"{client_ip}:{request.url.path}"
        allowed, retry_after, remaining = await limiter.check(key)

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(requests_per_minute),
                    "X-RateLimit-Remaining": "0",
                },
            )

    return dependency

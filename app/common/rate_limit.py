"""Redis-backed fixed-window rate limiting."""

from dataclasses import dataclass

from redis.asyncio import Redis

from app.exceptions import DependencyUnavailableError, RateLimitExceededError


@dataclass(frozen=True, slots=True)
class Limit:
    """Parsed rate-limit policy."""

    requests: int
    window_seconds: int


def parse_limit(value: str) -> Limit:
    """Parse policies such as ``30/minute`` and ``5/hour``."""
    try:
        amount_text, unit = value.lower().split("/", maxsplit=1)
        amount = int(amount_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid rate limit: {value}") from exc
    windows = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
    if amount < 1 or unit not in windows:
        raise ValueError(f"Invalid rate limit: {value}")
    return Limit(amount, windows[unit])


class RateLimiter:
    """Atomic per-principal fixed-window limiter."""

    def __init__(self, redis: Redis, prefix: str, fail_open: bool) -> None:
        self._redis = redis
        self._prefix = prefix
        self._fail_open = fail_open

    async def check(self, bucket: str, principal: str, policy: str) -> None:
        """Consume one request or raise a stable domain error."""
        limit = parse_limit(policy)
        key = f"{self._prefix}:rate:{bucket}:{principal}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, limit.window_seconds, nx=True)
                count, _ = await pipe.execute()
        except Exception as exc:
            if self._fail_open:
                return
            raise DependencyUnavailableError("Rate-limit service unavailable") from exc
        if int(count) > limit.requests:
            raise RateLimitExceededError(details={"retry_after": limit.window_seconds})


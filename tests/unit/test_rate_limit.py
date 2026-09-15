"""Rate-policy and Redis degradation unit tests."""

import unittest

from app.common.rate_limit import RateLimiter, parse_limit
from app.exceptions import DependencyUnavailableError, RateLimitExceededError


class _Pipeline:
    def __init__(self, count: int = 1, failure: Exception | None = None) -> None:
        self.count = count
        self.failure = failure

    async def __aenter__(self) -> "_Pipeline":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def incr(self, _: str) -> None:
        return None

    def expire(self, *_: object, **__: object) -> None:
        return None

    async def execute(self) -> tuple[int, bool]:
        if self.failure:
            raise self.failure
        return self.count, True


class _Redis:
    def __init__(self, pipeline: _Pipeline) -> None:
        self._pipeline = pipeline

    def pipeline(self, **_: object) -> _Pipeline:
        return self._pipeline


class RateLimitTests(unittest.IsolatedAsyncioTestCase):
    """Verify parsing, rejection and explicit Redis degradation policy."""

    def test_parse_limit(self) -> None:
        parsed = parse_limit("30/minute")
        self.assertEqual((parsed.requests, parsed.window_seconds), (30, 60))
        with self.assertRaises(ValueError):
            parse_limit("zero/fortnight")

    async def test_limit_rejection_includes_retry_after(self) -> None:
        limiter = RateLimiter(_Redis(_Pipeline(count=3)), "test", False)  # type: ignore[arg-type]
        with self.assertRaises(RateLimitExceededError) as raised:
            await limiter.check("auth", "client", "2/minute")
        self.assertEqual(raised.exception.details, {"retry_after": 60})

    async def test_redis_failure_policy(self) -> None:
        redis = _Redis(_Pipeline(failure=RuntimeError("offline")))
        await RateLimiter(redis, "test", True).check(  # type: ignore[arg-type]
            "search", "client", "2/minute"
        )
        with self.assertRaises(DependencyUnavailableError):
            await RateLimiter(redis, "test", False).check(  # type: ignore[arg-type]
                "search", "client", "2/minute"
            )


if __name__ == "__main__":
    unittest.main()

"""Namespaced JSON cache adapter."""

import json
from typing import Any

from redis.asyncio import Redis


class Cache:
    """Small explicit cache abstraction; PostgreSQL remains authoritative."""

    def __init__(self, redis: Redis, prefix: str) -> None:
        self._redis = redis
        self._prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self._prefix}:cache:{key}"

    async def get_json(self, key: str) -> Any | None:
        """Return decoded cached JSON when present."""
        raw = await self._redis.get(self._key(key))
        if raw is None:
            return None
        return json.loads(raw)

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Cache a JSON value with an explicit expiry."""
        await self._redis.set(
            self._key(key),
            json.dumps(value, separators=(",", ":"), default=str),
            ex=ttl_seconds,
        )

    async def delete(self, *keys: str) -> None:
        """Delete explicit cache keys."""
        if keys:
            await self._redis.delete(*(self._key(key) for key in keys))


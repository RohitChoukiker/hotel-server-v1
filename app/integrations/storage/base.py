"""Object storage contract for immutable import inputs."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol


class ObjectStorage(Protocol):
    """Store and materialize immutable raw import objects."""

    async def put(self, object_key: str, chunks: AsyncIterator[bytes]) -> tuple[str, int]:
        """Store chunks and return SHA-256 plus byte size."""

    async def materialize(self, object_key: str) -> Path:
        """Return a local read-compatible path for worker processing."""

    async def release(self, path: Path) -> None:
        """Release a worker-local materialization when required."""

    async def delete(self, object_key: str) -> None:
        """Delete a newly rejected object by its exact key."""

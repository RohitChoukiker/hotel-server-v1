"""Local durable-volume object storage adapter."""

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import aiofiles


class LocalObjectStorage:
    """Store immutable imports beneath a configured volume."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _resolve(self, object_key: str) -> Path:
        target = (self._root / object_key).resolve()
        if self._root not in target.parents:
            raise ValueError("Unsafe object key")
        return target

    async def put(self, object_key: str, chunks: AsyncIterator[bytes]) -> tuple[str, int]:
        """Write a new object without overwriting an existing raw file."""
        target = self._resolve(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        try:
            async with aiofiles.open(target, "xb") as output:
                async for chunk in chunks:
                    digest.update(chunk)
                    size += len(chunk)
                    await output.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        target.chmod(0o600)
        return digest.hexdigest(), size

    async def materialize(self, object_key: str) -> Path:
        """Return the existing local object path."""
        target = self._resolve(object_key)
        if not target.is_file():
            raise FileNotFoundError(target)
        return target

    async def release(self, path: Path) -> None:
        """Retain durable local objects after a worker finishes reading."""
        del path

    async def delete(self, object_key: str) -> None:
        """Delete one exact rejected local object."""
        self._resolve(object_key).unlink(missing_ok=True)

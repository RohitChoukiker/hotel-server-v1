"""Google Cloud Storage adapter for production import archives."""

import asyncio
import hashlib
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import aiofiles
from google.cloud import storage


class GCSObjectStorage:
    """Persist immutable import inputs in a configured GCS bucket."""

    def __init__(self, bucket_name: str) -> None:
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket_name)

    async def put(self, object_key: str, chunks: AsyncIterator[bytes]) -> tuple[str, int]:
        """Spool an async upload and atomically create a GCS object."""
        digest = hashlib.sha256()
        size = 0
        with tempfile.NamedTemporaryFile(delete=False) as temporary:
            path = Path(temporary.name)
        try:
            async with aiofiles.open(path, "wb") as output:
                async for chunk in chunks:
                    digest.update(chunk)
                    size += len(chunk)
                    await output.write(chunk)
            blob = self._bucket.blob(object_key)
            await asyncio.to_thread(blob.upload_from_filename, str(path), if_generation_match=0)
        finally:
            path.unlink(missing_ok=True)
        return digest.hexdigest(), size

    async def materialize(self, object_key: str) -> Path:
        """Download a worker-local copy of an immutable GCS object."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as temporary:
            path = Path(temporary.name)
        blob = self._bucket.blob(object_key)
        try:
            await asyncio.to_thread(blob.download_to_filename, str(path))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path

    async def release(self, path: Path) -> None:
        """Delete a temporary GCS materialization without deleting its archive."""
        path.unlink(missing_ok=True)

    async def delete(self, object_key: str) -> None:
        """Delete one exact newly rejected GCS object."""
        await asyncio.to_thread(self._bucket.blob(object_key).delete)

"""Stage a hotel CSV through the same production import service."""

import argparse
import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import aiofiles

from app.config import get_settings
from app.database import create_engine, create_session_factory
from app.enums import ImportType
from app.integrations.storage.gcs import GCSObjectStorage
from app.integrations.storage.local import LocalObjectStorage
from app.services.imports import ImportService


async def chunks(path: Path) -> AsyncIterator[bytes]:
    """Stream a local CSV in bounded chunks."""
    async with aiofiles.open(path, "rb") as source:
        while chunk := await source.read(1024 * 1024):
            yield chunk


async def run(path: Path, country_id: uuid.UUID, region_id: uuid.UUID) -> None:
    """Create a pending hotel import job."""
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    storage = (
        GCSObjectStorage(settings.storage.gcs_bucket)
        if settings.storage.backend == "gcs" and settings.storage.gcs_bucket
        else LocalObjectStorage(settings.storage.local_path)
    )
    try:
        async with factory() as session:
            job = await ImportService(session, storage).create_job(
                ImportType.HOTELS,
                "TRIPADVISOR",
                path.name,
                chunks(path),
                country_id,
                region_id,
                None,
            )
            print(job.id)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("--country-id", type=uuid.UUID, required=True)
    parser.add_argument("--region-id", type=uuid.UUID, required=True)
    arguments = parser.parse_args()
    asyncio.run(run(arguments.csv, arguments.country_id, arguments.region_id))

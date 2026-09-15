"""Production-safe immutable CSV staging and job creation."""

import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.logging import get_logger
from app.enums import ImportType, JobStatus
from app.exceptions import ImportValidationError, UnsupportedSourceError
from app.integrations.storage.base import ObjectStorage
from app.models import ImportJob
from app.repositories.catalog import SourceRepository
from app.repositories.operations import AuditRepository, ImportJobRepository


class ImportService:
    """Validate and stage CSV import jobs; processing happens in Celery."""

    def __init__(self, session: AsyncSession, storage: ObjectStorage) -> None:
        self._session = session
        self._storage = storage
        self._jobs = ImportJobRepository(session)
        self._sources = SourceRepository(session)
        self._audit = AuditRepository(session)

    async def create_job(
        self,
        import_type: ImportType,
        source_code: str,
        file_name: str,
        chunks: AsyncIterator[bytes],
        country_id: uuid.UUID | None,
        region_id: uuid.UUID | None,
        actor_user_id: uuid.UUID | None = None,
    ) -> ImportJob:
        """Copy a CSV unchanged to immutable storage and create a pending job."""
        if Path(file_name).suffix.casefold() != ".csv":
            raise ImportValidationError("Only CSV files are accepted")
        source = await self._sources.get_by_code(source_code)
        if source is None:
            raise UnsupportedSourceError()
        source_id = source.id
        # Do not hold a database transaction while streaming to object storage.
        await self._session.rollback()
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(file_name).name)
        job_id = uuid.uuid4()
        object_key = f"raw/{import_type.value.lower()}/{job_id}/{safe_name}"
        digest, size = await self._storage.put(object_key, chunks)
        if size == 0:
            await self._storage.delete(object_key)
            raise ImportValidationError("CSV file is empty")
        job = ImportJob(
            id=job_id,
            status=JobStatus.PENDING.value,
            import_type=import_type.value,
            source_id=source_id,
            country_id=country_id,
            region_id=region_id,
            file_name=safe_name,
            object_key=object_key,
            content_sha256=digest,
            records_read=0,
            inserted=0,
            updated=0,
            skipped=0,
            failed=0,
            progress=0,
            checkpoint={"row": 0},
        )
        try:
            await self._jobs.add(job)
            if actor_user_id is not None:
                await self._audit.record(
                    actor_user_id,
                    "import_started",
                    "import_job",
                    str(job.id),
                    {"import_type": import_type.value, "file_name": safe_name},
                )
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            try:
                await self._storage.delete(object_key)
            except Exception:
                get_logger().exception(
                    "rejected_import_cleanup_failed", object_key=object_key
                )
            raise
        return job

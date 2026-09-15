"""Scraper monitoring and retry business logic."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.dto.admin import ScrapeFailureRead, ScrapeRunCreate, ScrapeRunRead
from app.enums import JobStatus
from app.exceptions import NotFoundError, UnsupportedSourceError
from app.models import ScrapeRun
from app.repositories.catalog import SourceRepository
from app.repositories.operations import AuditRepository, ScrapeRepository


class ScrapeService:
    """Expose scraper state and audit retry requests."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._scrapes = ScrapeRepository(session)
        self._sources = SourceRepository(session)
        self._audit = AuditRepository(session)

    async def start(
        self, actor_id: uuid.UUID, payload: ScrapeRunCreate
    ) -> ScrapeRunRead:
        """Create a bounded run without inferring or inventing source geo IDs."""
        source = await self._sources.get_by_code(payload.source_code)
        if source is None or source.code != "TRIPADVISOR":
            raise UnsupportedSourceError()
        run = ScrapeRun(
            id=uuid.uuid4(),
            source_id=source.id,
            status=JobStatus.PENDING.value,
            scope={
                "source_code": source.code,
                "geo_id": payload.geo_id,
                "start_offset": payload.start_offset,
                "page_size": payload.page_size,
                "max_pages": payload.max_pages,
                "extra_variables": payload.extra_variables,
            },
            processed_count=0,
            success_count=0,
            failure_count=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await self._scrapes.add_run(run)
        await self._audit.record(
            actor_id,
            "scrape_started",
            "scrape_run",
            str(run.id),
            {"source_code": source.code, "geo_id": payload.geo_id},
        )
        await self._session.commit()
        return ScrapeRunRead.model_validate(run)

    async def list_runs(self, limit: int = 100) -> list[ScrapeRunRead]:
        """List recent scrape runs."""
        return [ScrapeRunRead.model_validate(item) for item in await self._scrapes.list_runs(limit)]

    async def run(self, run_id: uuid.UUID) -> ScrapeRunRead:
        """Return one scrape run."""
        run = await self._scrapes.get_run(run_id)
        if run is None:
            raise NotFoundError("Scrape run not found")
        return ScrapeRunRead.model_validate(run)

    async def failures(self, run_id: uuid.UUID) -> list[ScrapeFailureRead]:
        """Return all failures for one existing run."""
        await self.run(run_id)
        return [
            ScrapeFailureRead.model_validate(item)
            for item in await self._scrapes.failures(run_id)
        ]

    async def retry_failures(
        self, actor_id: uuid.UUID, run_id: uuid.UUID
    ) -> int:
        """Select unresolved failures and append a privileged audit record."""
        await self.run(run_id)
        failures = await self._scrapes.failures(run_id, unresolved_only=True)
        await self._audit.record(
            actor_id,
            "scrape_retry_triggered",
            "scrape_run",
            str(run_id),
            {"failure_count": len(failures)},
        )
        await self._session.commit()
        return len(failures)

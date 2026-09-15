"""Checkpointed upstream scraper orchestration isolated from HTTP and Celery."""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.logging import get_logger
from app.enums import JobStatus
from app.integrations.scraper.tripadvisor import TripAdvisorAdapter
from app.integrations.storage.base import ObjectStorage
from app.models import ScrapeFailure, ScrapeRun
from app.repositories.operations import ScrapeRepository


async def _single_chunk(content: bytes) -> AsyncIterator[bytes]:
    yield content


class ScrapeOrchestrator:
    """Fetch bounded listing pages with durable page-level checkpoints."""

    partition_key = "hotel-listings"

    def __init__(
        self,
        session: AsyncSession,
        adapter: TripAdvisorAdapter,
        storage: ObjectStorage,
    ) -> None:
        self._session = session
        self._adapter = adapter
        self._storage = storage
        self._scrapes = ScrapeRepository(session)

    async def run(self, run_id: uuid.UUID) -> int:
        """Resume one run from its last committed successful offset."""
        run = await self._scrapes.get_run(run_id)
        if run is None:
            raise ValueError(f"Scrape run {run_id} does not exist")
        if run.status == JobStatus.COMPLETED.value:
            return run.success_count

        scope = run.scope
        geo_id = str(scope["geo_id"])
        page_size = int(scope.get("page_size", 30))
        max_pages = int(scope.get("max_pages", 1))
        extra_variables = dict(scope.get("extra_variables", {}))
        checkpoint = await self._scrapes.checkpoint(run_id, self.partition_key)
        state = checkpoint.checkpoint if checkpoint else {}
        offset = int(state.get("next_offset", scope.get("start_offset", 0)))
        pages_completed = int(state.get("pages_completed", 0))

        run.status = JobStatus.RUNNING.value
        run.started_at = run.started_at or datetime.now(UTC)
        await self._session.commit()
        get_logger().info(
            "scrape_started", run_id=str(run_id), geo_id=geo_id, offset=offset
        )

        while pages_completed < max_pages:
            try:
                payload = await self._adapter.fetch_listing_page(
                    geo_id,
                    offset,
                    page_size,
                    extra_variables,
                )
                raw = json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
                object_key = (
                    f"scrapes/{run_id}/listings/offset-{offset}-{timestamp}.json"
                )
                await self._storage.put(object_key, _single_chunk(raw))
            except Exception as exc:
                await self._record_failure(run, offset, exc)
                raise

            failure = await self._scrapes.unresolved_failure(
                run_id, f"offset:{offset}"
            )
            if failure is not None:
                failure.resolved_at = datetime.now(UTC)
            pages_completed += 1
            offset += page_size
            run.processed_count += 1
            run.success_count += 1
            await self._scrapes.upsert_checkpoint(
                run_id,
                self.partition_key,
                {
                    "next_offset": offset,
                    "pages_completed": pages_completed,
                    "last_raw_object_key": object_key,
                },
            )
            await self._session.commit()

        run.status = JobStatus.COMPLETED.value
        run.completed_at = datetime.now(UTC)
        await self._session.commit()
        get_logger().info(
            "scrape_completed", run_id=str(run_id), pages=run.success_count
        )
        return run.success_count

    async def mark_failed(self, run_id: uuid.UUID) -> None:
        """Mark a retry-exhausted run as terminally failed."""
        run = await self._scrapes.get_run(run_id)
        if run is None or run.status == JobStatus.COMPLETED.value:
            return
        run.status = JobStatus.FAILED.value
        run.completed_at = datetime.now(UTC)
        await self._session.commit()

    async def _record_failure(
        self, run: ScrapeRun, offset: int, exc: Exception
    ) -> None:
        now = datetime.now(UTC)
        entity_id = f"offset:{offset}"
        failure = await self._scrapes.unresolved_failure(run.id, entity_id)
        if failure is None:
            failure = ScrapeFailure(
                id=uuid.uuid4(),
                scrape_run_id=run.id,
                source_entity_id=entity_id,
                error_code=type(exc).__name__,
                error_message=str(exc)[:4000],
                retry_count=0,
                next_retry_at=now + timedelta(seconds=60),
                created_at=now,
                updated_at=now,
            )
            await self._scrapes.add_failure(failure)
        else:
            failure.retry_count += 1
            delay = min(3600, 60 * (2 ** failure.retry_count))
            failure.error_code = type(exc).__name__
            failure.error_message = str(exc)[:4000]
            failure.next_retry_at = now + timedelta(seconds=delay)
        run.processed_count += 1
        run.failure_count += 1
        run.status = JobStatus.PARTIAL.value
        await self._session.commit()
        get_logger().error(
            "scrape_failed",
            run_id=str(run.id),
            offset=offset,
            error_type=type(exc).__name__,
        )

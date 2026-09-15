"""Celery jobs for imports, extraction, scoring, normalization, and retries."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

import httpx
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import LockError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.logging import get_logger
from app.config import Settings, get_settings
from app.database import create_engine, create_session_factory
from app.enums import ScopeType
from app.exceptions import DependencyUnavailableError, SourceRateLimitError
from app.integrations.llm.deterministic import DeterministicTextInterpreter
from app.integrations.llm.openai_structured import OpenAIStructuredInterpreter
from app.integrations.scraper.tripadvisor import TripAdvisorAdapter
from app.integrations.storage.gcs import GCSObjectStorage
from app.integrations.storage.local import LocalObjectStorage
from app.models import ImportJob, ScrapeFailure, ScrapeRun
from app.orchestrators.imports import CSVImportOrchestrator
from app.orchestrators.scraper import ScrapeOrchestrator
from app.services.data_quality import DataQualityService
from app.services.scoring import ScoringService
from app.workers.celery_app import celery_app

T = TypeVar("T")


def _run(work: Callable[[AsyncSession, Settings], Awaitable[T]]) -> T:
    """Run one isolated async unit with a task-local engine."""
    async def execute() -> T:
        settings = get_settings()
        engine = create_engine(settings)
        factory = create_session_factory(engine)
        try:
            async with factory() as session:
                return await work(session, settings)
        finally:
            await engine.dispose()

    return asyncio.run(execute())


def _storage(settings: Settings) -> Any:
    if settings.storage.backend == "gcs":
        if not settings.storage.gcs_bucket:
            raise ValueError("GCS bucket is not configured")
        return GCSObjectStorage(settings.storage.gcs_bucket)
    return LocalObjectStorage(settings.storage.local_path)


async def _interpreter(settings: Settings, client: httpx.AsyncClient) -> Any:
    if settings.llm.provider == "openai":
        return OpenAIStructuredInterpreter(settings.llm, client)
    return DeterministicTextInterpreter()


@celery_app.task(name="app.workers.tasks.import_csv")
def import_csv(job_id: str) -> None:
    """Process a staged, resumable CSV import."""
    async def work(session: AsyncSession, settings: Settings) -> str | None:
        job = await session.get(ImportJob, uuid.UUID(job_id))
        if job is None:
            return None
        import_type = job.import_type
        await CSVImportOrchestrator(
            session,
            _storage(settings),
            settings.worker.import_batch_size,
        ).run(uuid.UUID(job_id))
        return import_type

    import_type = _run(work)
    if import_type == "REVIEWS":
        celery_app.send_task("app.workers.tasks.process_reviews")


@celery_app.task(
    bind=True,
    name="app.workers.tasks.process_reviews",
    max_retries=5,
)
def process_reviews(self: Any) -> int:
    """Run structured attribute extraction outside HTTP requests."""
    async def work(session: AsyncSession, settings: Settings) -> tuple[int, bool]:
        async with httpx.AsyncClient() as client:
            interpreter = await _interpreter(settings, client)
            service = ScoringService(session, interpreter)
            processed = await service.process_reviews()
            pending = await service.has_pending_reviews()
            if not pending:
                await service.recalculate_all()
                await service.normalize(ScopeType.GLOBAL)
            return processed, pending

    settings = get_settings()
    redis = Redis.from_url(settings.redis.url.get_secret_value())
    lock = redis.lock(
        f"{settings.redis.cache_prefix}:task-lock:process-reviews",
        timeout=settings.worker.task_time_limit_seconds + 60,
    )
    acquired = False
    try:
        acquired = bool(lock.acquire(blocking=False))
        if not acquired:
            get_logger().info("task_already_running", task="process_reviews")
            return 0
        processed, pending = _run(work)
        if pending:
            celery_app.send_task("app.workers.tasks.process_reviews", countdown=1)
        return processed
    except Exception as exc:
        countdown = min(1800, 30 * (2 ** self.request.retries))
        get_logger().warning(
            "retry_scheduled",
            task="process_reviews",
            countdown=countdown,
            retry=self.request.retries + 1,
        )
        raise self.retry(exc=exc, countdown=countdown) from exc
    finally:
        if acquired:
            try:
                lock.release()
            except LockError:
                get_logger().warning("task_lock_expired", task="process_reviews")
        redis.close()


@celery_app.task(name="app.workers.tasks.recalculate_hotel")
def recalculate_hotel(hotel_id: str) -> int:
    """Recalculate one hotel's precomputed attribute aggregates."""
    async def work(session: AsyncSession, settings: Settings) -> int:
        del settings
        return await ScoringService(session, DeterministicTextInterpreter()).recalculate_hotel(
            uuid.UUID(hotel_id)
        )

    return _run(work)


@celery_app.task(name="app.workers.tasks.normalize_scores")
def normalize_scores(
    scope: str = "GLOBAL",
    country_id: str | None = None,
    region_id: str | None = None,
    city_id: str | None = None,
    hotel_type: str | None = None,
    hotel_ids: list[str] | None = None,
) -> int:
    """Bell-curve normalize precomputed hotel scores."""
    async def work(session: AsyncSession, settings: Settings) -> int:
        del settings
        return await ScoringService(session, DeterministicTextInterpreter()).normalize(
            ScopeType(scope),
            uuid.UUID(country_id) if country_id else None,
            uuid.UUID(region_id) if region_id else None,
            uuid.UUID(city_id) if city_id else None,
            hotel_type,
            [uuid.UUID(item) for item in hotel_ids] if hotel_ids else None,
        )

    return _run(work)


@celery_app.task(name="app.workers.tasks.scan_data_quality")
def scan_data_quality() -> int:
    """Run database-level quality checks outside HTTP requests."""
    async def work(session: AsyncSession, settings: Settings) -> int:
        del settings
        return await DataQualityService(session).scan()

    return _run(work)


@celery_app.task(bind=True, name="app.workers.tasks.run_scrape", max_retries=10)
def run_scrape(self: Any, run_id: str) -> int:
    """Execute a bounded scrape with per-source distributed concurrency slots."""
    parsed_run_id = uuid.UUID(run_id)

    async def work(session: AsyncSession, settings: Settings) -> int:
        run = await session.get(ScrapeRun, parsed_run_id)
        if run is None:
            return 0
        redis = AsyncRedis.from_url(settings.redis.url.get_secret_value())
        lock = None
        try:
            for slot in range(settings.worker.scraper_concurrency):
                candidate = redis.lock(
                    f"{settings.redis.cache_prefix}:scrape:{run.source_id}:slot:{slot}",
                    timeout=settings.worker.task_time_limit_seconds + 60,
                )
                if await candidate.acquire(blocking=False):
                    lock = candidate
                    break
            if lock is None:
                raise DependencyUnavailableError(
                    "Per-source scraper concurrency limit reached"
                )
            async with httpx.AsyncClient() as client:
                adapter = TripAdvisorAdapter(settings.scraper, client)
                return await ScrapeOrchestrator(
                    session, adapter, _storage(settings)
                ).run(parsed_run_id)
        finally:
            if lock is not None:
                try:
                    await lock.release()
                except LockError:
                    get_logger().warning(
                        "scrape_lock_expired", run_id=str(parsed_run_id)
                    )
            await redis.aclose()

    try:
        return _run(work)
    except Exception as exc:
        settings = get_settings()
        if self.request.retries >= settings.scraper.max_retries:
            async def mark_failed(session: AsyncSession, task_settings: Settings) -> None:
                async with httpx.AsyncClient() as client:
                    orchestrator = ScrapeOrchestrator(
                        session,
                        TripAdvisorAdapter(task_settings.scraper, client),
                        _storage(task_settings),
                    )
                    await orchestrator.mark_failed(parsed_run_id)

            _run(mark_failed)
            raise
        if isinstance(exc, SourceRateLimitError):
            countdown = exc.retry_after_seconds
        else:
            countdown = min(3600, 60 * (2 ** self.request.retries))
        get_logger().warning(
            "retry_scheduled",
            task="run_scrape",
            run_id=run_id,
            countdown=countdown,
            retry=self.request.retries + 1,
        )
        raise self.retry(exc=exc, countdown=countdown) from exc


@celery_app.task(name="app.workers.tasks.retry_scrape_failure")
def retry_scrape_failure(failure_id: str) -> None:
    """Compatibility task that resumes the failure's checkpointed parent run."""
    async def work(session: AsyncSession, settings: Settings) -> str | None:
        del settings
        failure = await session.get(ScrapeFailure, uuid.UUID(failure_id))
        if failure is None or failure.resolved_at is not None:
            return None
        run = await session.get(ScrapeRun, failure.scrape_run_id)
        if run is None:
            return None
        return str(run.id)

    run_id = _run(work)
    if run_id is not None:
        celery_app.send_task("app.workers.tasks.run_scrape", args=[run_id])


@celery_app.task(name="app.workers.tasks.worker_heartbeat")
def worker_heartbeat() -> None:
    """Publish a short-lived worker readiness heartbeat."""
    settings = get_settings()
    client = Redis.from_url(settings.redis.url.get_secret_value())
    try:
        client.set(
            f"{settings.redis.cache_prefix}:worker:heartbeat",
            datetime.now(UTC).isoformat(),
            ex=90,
        )
    finally:
        client.close()

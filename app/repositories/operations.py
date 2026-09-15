"""Operational jobs, audit, and dashboard persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AlgorithmVersion,
    AuditLog,
    City,
    DataQualityIssue,
    Hotel,
    HotelSourceMapping,
    ImportJob,
    Region,
    Review,
    ReviewProcessingStatus,
    ScrapeCheckpoint,
    ScrapeFailure,
    ScrapeRun,
    SystemSetting,
    User,
)


class ImportJobRepository:
    """Persist and query resumable CSV import jobs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, job: ImportJob) -> ImportJob:
        """Stage a new import job."""
        self._session.add(job)
        await self._session.flush()
        return job

    async def get(self, job_id: uuid.UUID) -> ImportJob | None:
        """Return one import job."""
        return await self._session.get(ImportJob, job_id)

    async def list(self, limit: int, cursor_id: uuid.UUID | None) -> list[ImportJob]:
        """Keyset-page import jobs."""
        query = select(ImportJob).order_by(ImportJob.id.desc()).limit(limit + 1)
        if cursor_id:
            query = query.where(ImportJob.id < cursor_id)
        return list((await self._session.scalars(query)).all())


class ScrapeRepository:
    """Persist scraper runs and retryable failures."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_run(self, run: ScrapeRun) -> ScrapeRun:
        """Stage a new explicitly scoped scraper run."""
        self._session.add(run)
        await self._session.flush()
        return run

    async def list_runs(self, limit: int) -> list[ScrapeRun]:
        """List recent scraper runs."""
        return list(
            (
                await self._session.scalars(
                    select(ScrapeRun).order_by(ScrapeRun.created_at.desc()).limit(limit)
                )
            ).all()
        )

    async def get_run(self, run_id: uuid.UUID) -> ScrapeRun | None:
        """Return one scraper run."""
        return await self._session.get(ScrapeRun, run_id)

    async def failures(
        self, run_id: uuid.UUID, unresolved_only: bool = False
    ) -> list[ScrapeFailure]:
        """Return scraper failures for a run."""
        query = select(ScrapeFailure).where(ScrapeFailure.scrape_run_id == run_id)
        if unresolved_only:
            query = query.where(ScrapeFailure.resolved_at.is_(None))
        return list((await self._session.scalars(query.order_by(ScrapeFailure.created_at))).all())

    async def checkpoint(
        self, run_id: uuid.UUID, partition_key: str
    ) -> ScrapeCheckpoint | None:
        """Return the latest durable checkpoint for one partition."""
        return await self._session.scalar(
            select(ScrapeCheckpoint).where(
                ScrapeCheckpoint.scrape_run_id == run_id,
                ScrapeCheckpoint.partition_key == partition_key,
            )
        )

    async def upsert_checkpoint(
        self, run_id: uuid.UUID, partition_key: str, checkpoint: dict[str, Any]
    ) -> None:
        """Persist a checkpoint idempotently after a successful page."""
        now = datetime.now(UTC)
        statement = insert(ScrapeCheckpoint).values(
            id=uuid.uuid4(),
            scrape_run_id=run_id,
            partition_key=partition_key,
            checkpoint=checkpoint,
            created_at=now,
            updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["scrape_run_id", "partition_key"],
            set_={"checkpoint": checkpoint, "updated_at": now},
        )
        await self._session.execute(statement)

    async def unresolved_failure(
        self, run_id: uuid.UUID, source_entity_id: str
    ) -> ScrapeFailure | None:
        """Return an existing unresolved partition failure."""
        return await self._session.scalar(
            select(ScrapeFailure).where(
                ScrapeFailure.scrape_run_id == run_id,
                ScrapeFailure.source_entity_id == source_entity_id,
                ScrapeFailure.resolved_at.is_(None),
            )
        )

    async def add_failure(self, failure: ScrapeFailure) -> None:
        """Persist a retryable source failure."""
        self._session.add(failure)
        await self._session.flush()


class DataQualityRepository:
    """Persist source data failures instead of discarding rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        issue_type: str,
        severity: str,
        details: dict[str, Any],
        source_id: uuid.UUID | None = None,
        import_job_id: uuid.UUID | None = None,
        entity_type: str | None = None,
        source_entity_id: str | None = None,
    ) -> DataQualityIssue:
        """Stage a data-quality issue."""
        issue = DataQualityIssue(
            issue_type=issue_type,
            severity=severity,
            details=details,
            source_id=source_id,
            import_job_id=import_job_id,
            entity_type=entity_type,
            source_entity_id=source_entity_id,
        )
        self._session.add(issue)
        await self._session.flush()
        return issue

    async def list(
        self, issue_type: str | None, unresolved_only: bool, limit: int
    ) -> list[DataQualityIssue]:
        """Filter recent quality issues."""
        query = select(DataQualityIssue)
        if issue_type:
            query = query.where(DataQualityIssue.issue_type == issue_type)
        if unresolved_only:
            query = query.where(DataQualityIssue.resolved_at.is_(None))
        return list(
            (
                await self._session.scalars(
                    query.order_by(DataQualityIssue.created_at.desc()).limit(limit)
                )
            ).all()
        )

    async def unresolved_exists(
        self,
        issue_type: str,
        entity_type: str,
        source_entity_id: str,
    ) -> bool:
        """Prevent repeated scans from duplicating an unresolved finding."""
        count = await self._session.scalar(
            select(func.count())
            .select_from(DataQualityIssue)
            .where(
                DataQualityIssue.issue_type == issue_type,
                DataQualityIssue.entity_type == entity_type,
                DataQualityIssue.source_entity_id == source_entity_id,
                DataQualityIssue.resolved_at.is_(None),
            )
        )
        return bool(count)

    async def detect(self) -> list[dict[str, Any]]:
        """Detect database-level duplicate, orphan and invariant violations."""
        findings: list[dict[str, Any]] = []
        normalized_name = func.lower(func.trim(Hotel.name))
        duplicate_hotels = await self._session.execute(
            select(
                Hotel.city_id,
                normalized_name.label("name"),
                func.array_agg(cast(Hotel.id, String)).label("ids"),
                func.count().label("count"),
            )
            .where(Hotel.deleted_at.is_(None))
            .group_by(Hotel.city_id, normalized_name, Hotel.latitude, Hotel.longitude)
            .having(func.count() > 1)
        )
        for row in duplicate_hotels:
            findings.append(
                {
                    "issue_type": "DUPLICATE_HOTEL",
                    "entity_type": "HOTEL",
                    "source_entity_id": str(row.ids[0]),
                    "details": {"hotel_ids": row.ids, "count": row.count},
                }
            )

        duplicate_reviews = await self._session.execute(
            select(
                Review.source_id,
                Review.source_review_id,
                func.count().label("count"),
            )
            .group_by(Review.source_id, Review.source_review_id)
            .having(func.count() > 1)
        )
        for row in duplicate_reviews:
            findings.append(
                {
                    "issue_type": "DUPLICATE_REVIEW",
                    "entity_type": "REVIEW",
                    "source_entity_id": row.source_review_id,
                    "details": {"source_id": str(row.source_id), "count": row.count},
                }
            )

        mapped_hotel = select(HotelSourceMapping.hotel_id).where(
            HotelSourceMapping.hotel_id == Hotel.id,
            HotelSourceMapping.is_active,
        )
        missing_mappings = await self._session.scalars(
            select(Hotel.id).where(
                Hotel.deleted_at.is_(None), ~mapped_hotel.exists()
            )
        )
        for hotel_id in missing_mappings:
            findings.append(
                {
                    "issue_type": "MISSING_SOURCE_MAPPING",
                    "entity_type": "HOTEL",
                    "source_entity_id": str(hotel_id),
                    "details": {"hotel_id": str(hotel_id)},
                }
            )

        invalid_hotels = await self._session.execute(
            select(Hotel.id, Hotel.latitude, Hotel.longitude).where(
                or_(
                    Hotel.latitude < -90,
                    Hotel.latitude > 90,
                    Hotel.longitude < -180,
                    Hotel.longitude > 180,
                )
            )
        )
        for row in invalid_hotels:
            issue_type = (
                "INVALID_LATITUDE"
                if not -90 <= float(row.latitude) <= 90
                else "INVALID_LONGITUDE"
            )
            findings.append(
                {
                    "issue_type": issue_type,
                    "entity_type": "HOTEL",
                    "source_entity_id": str(row.id),
                    "details": {
                        "latitude": float(row.latitude),
                        "longitude": float(row.longitude),
                    },
                }
            )

        invalid_reviews = await self._session.execute(
            select(Review.id, Review.source_review_id).where(
                or_(
                    Review.source_rating < 0,
                    Review.source_rating > Review.source_rating_scale,
                    Review.source_rating_scale <= 0,
                    Review.normalized_rating_5 < 0,
                    Review.normalized_rating_5 > 5,
                )
            )
        )
        for row in invalid_reviews:
            findings.append(
                {
                    "issue_type": "INVALID_REVIEW_RATING",
                    "entity_type": "REVIEW",
                    "source_entity_id": row.source_review_id,
                    "details": {"review_id": str(row.id)},
                }
            )

        exhausted_processing = await self._session.execute(
            select(
                ReviewProcessingStatus.review_id,
                ReviewProcessingStatus.error,
                ReviewProcessingStatus.attempt_count,
            ).where(
                ReviewProcessingStatus.status == "FAILED",
                ReviewProcessingStatus.attempt_count >= 5,
            )
        )
        for row in exhausted_processing:
            findings.append(
                {
                    "issue_type": "REVIEW_PROCESSING_FAILED",
                    "entity_type": "REVIEW",
                    "source_entity_id": str(row.review_id),
                    "details": {
                        "attempt_count": row.attempt_count,
                        "error": row.error,
                    },
                }
            )

        existing_hotel = select(Hotel.id).where(Hotel.id == Review.hotel_id)
        orphan_reviews = await self._session.execute(
            select(Review.id, Review.source_review_id, Review.hotel_id).where(
                ~existing_hotel.exists()
            )
        )
        for row in orphan_reviews:
            findings.append(
                {
                    "issue_type": "ORPHAN_REVIEW",
                    "entity_type": "REVIEW",
                    "source_entity_id": row.source_review_id,
                    "details": {
                        "review_id": str(row.id),
                        "hotel_id": str(row.hotel_id),
                    },
                }
            )

        missing_city = select(City.id).where(City.id == Hotel.city_id)
        missing_region = select(Region.id).where(Region.id == Hotel.region_id)
        for issue_type, query in (
            (
                "MISSING_CITY",
                select(Hotel.id, Hotel.city_id).where(~missing_city.exists()),
            ),
            (
                "MISSING_REGION",
                select(Hotel.id, Hotel.region_id).where(~missing_region.exists()),
            ),
        ):
            for row in await self._session.execute(query):
                findings.append(
                    {
                        "issue_type": issue_type,
                        "entity_type": "HOTEL",
                        "source_entity_id": str(row.id),
                        "details": {"location_id": str(row[1])},
                    }
                )
        return findings


class AuditRepository:
    """Append-only privileged action audit trail."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        actor_user_id: uuid.UUID,
        action: str,
        resource_type: str,
        resource_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append an audit record."""
        self._session.add(
            AuditLog(
                actor_user_id=actor_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                metadata_payload=metadata or {},
                created_at=datetime.now(UTC),
            )
        )
        await self._session.flush()


class AdminRepository:
    """Operational aggregate queries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def dashboard(self) -> dict[str, int]:
        """Return core operational counters."""
        async def count(model: Any, *criteria: Any) -> int:
            value = await self._session.scalar(
                select(func.count()).select_from(model).where(*criteria)
            )
            return int(value or 0)

        return {
            "users": await count(User, User.deleted_at.is_(None)),
            "active_hotels": await count(Hotel, Hotel.is_active, Hotel.deleted_at.is_(None)),
            "reviews": await count(Review, Review.deleted_at.is_(None)),
            "unresolved_quality_issues": await count(
                DataQualityIssue, DataQualityIssue.resolved_at.is_(None)
            ),
            "running_imports": await count(ImportJob, ImportJob.status == "RUNNING"),
            "failed_scrapes": await count(ScrapeRun, ScrapeRun.status == "FAILED"),
        }

    async def algorithm_versions(self) -> list[AlgorithmVersion]:
        """Return all algorithm versions newest first."""
        return list(
            (
                await self._session.scalars(
                    select(AlgorithmVersion).order_by(AlgorithmVersion.version.desc())
                )
            ).all()
        )

    async def settings(self) -> list[SystemSetting]:
        """Return non-secret system settings."""
        return list(
            (
                await self._session.scalars(
                    select(SystemSetting).order_by(SystemSetting.key)
                )
            ).all()
        )

    async def upsert_setting(
        self, key: str, value: dict[str, Any], actor_id: uuid.UUID
    ) -> SystemSetting:
        """Create or replace a non-secret system setting."""
        statement = insert(SystemSetting).values(
            id=uuid.uuid4(),
            key=key,
            value=value,
            updated_by_user_id=actor_id,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["key"],
            set_={
                "value": value,
                "updated_by_user_id": actor_id,
                "updated_at": datetime.now(UTC),
            },
        )
        await self._session.execute(statement)
        setting = await self._session.scalar(
            select(SystemSetting).where(SystemSetting.key == key)
        )
        assert setting is not None
        return setting

"""Streamed JSONL imports for precomputed hotel sentiment."""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.logging import get_logger
from app.domain.sentiment import (
    AttributeSentimentValue,
    HotelSentimentRecord,
    aggregate_score_0_5,
    normalize_taxonomy_key,
)
from app.enums import JobStatus, ScopeType
from app.exceptions import ImportValidationError
from app.integrations.storage.base import ObjectStorage
from app.models import ImportJob
from app.repositories.catalog import HotelRepository
from app.repositories.operations import DataQualityRepository, ImportJobRepository
from app.repositories.sentiment import AmbiguousAttributeAlias, SentimentAggregateRepository
from app.services.aggregate_scoring import AggregateScoringService
from app.services.scoring import ScoreNormalizationService


class _UnknownHotelError(ImportValidationError):
    """Internal marker for an unmapped source hotel."""


@dataclass(slots=True)
class ImportCounterDelta:
    """Per-line counters applied only after a hotel savepoint succeeds."""

    hotels_matched: int = 0
    hotels_failed: int = 0
    attributes_seen: int = 0
    attributes_processed: int = 0
    attributes_inserted: int = 0
    attributes_updated: int = 0
    attributes_skipped_zero_mentions: int = 0
    attributes_failed: int = 0
    score_updates_applied: int = 0
    score_updates_skipped_precedence: int = 0
    unknown_hotels: int = 0
    auto_created_attributes: int = 0
    skipped: int = 0


class SentimentAnalysisImportOrchestrator:
    """Execute a resumable, per-hotel JSONL sentiment import."""

    def __init__(
        self,
        session: AsyncSession,
        storage: ObjectStorage,
        batch_size: int,
    ) -> None:
        self._session = session
        self._storage = storage
        self._batch_size = batch_size
        self._jobs = ImportJobRepository(session)
        self._hotels = HotelRepository(session)
        self._quality = DataQualityRepository(session)
        self._aggregates = SentimentAggregateRepository(session)
        self._scoring = AggregateScoringService(session)

    async def run(self, job_id: uuid.UUID) -> None:
        """Resume and execute a pending or partial sentiment import."""
        job = await self._jobs.get(job_id)
        if job is None:
            raise ImportValidationError("Import job not found")
        if job.status == JobStatus.COMPLETED.value:
            return
        job.status = JobStatus.RUNNING.value
        job.started_at = job.started_at or datetime.now(UTC)
        await self._session.commit()
        path = await self._storage.materialize(job.object_key)
        try:
            await self._read_lines(job, path)
            job.status = (
                JobStatus.PARTIAL.value
                if job.hotels_failed or job.attributes_failed
                else JobStatus.COMPLETED.value
            )
            job.progress = 100
            job.completed_at = datetime.now(UTC)
            await self._session.commit()
            if (
                job.status
                in {
                    JobStatus.COMPLETED.value,
                    JobStatus.PARTIAL.value,
                }
                and job.score_updates_applied > 0
            ):
                await ScoreNormalizationService(self._session).normalize(ScopeType.GLOBAL)
            get_logger().info(
                "sentiment_import_completed",
                job_id=str(job.id),
                inserted=job.attributes_inserted,
                updated=job.attributes_updated,
                failed=job.hotels_failed,
            )
        except Exception as exc:
            await self._mark_failed(job_id, exc)
            raise
        finally:
            await self._storage.release(path)

    async def _read_lines(self, job: ImportJob, path: Path) -> None:
        start_line = int((job.checkpoint or {}).get("line", 0))
        file_size = max(path.stat().st_size, 1)
        with path.open("r", encoding="utf-8-sig") as handle:
            line_number = 0
            while True:
                raw_line = handle.readline()
                if not raw_line:
                    break
                line_number += 1
                if line_number <= start_line or not raw_line.strip():
                    continue
                job.hotels_read += 1
                job.records_read = job.hotels_read
                try:
                    payload = json.loads(raw_line)
                    if not isinstance(payload, dict):
                        raise ImportValidationError("SENTIMENT_RECORD_MUST_BE_OBJECT")
                    record = HotelSentimentRecord.from_payload(payload)
                    async with self._session.begin_nested():
                        delta = await self._process_hotel(job, line_number, record)
                except (json.JSONDecodeError, ImportValidationError) as exc:
                    delta = ImportCounterDelta(hotels_failed=1)
                    if isinstance(exc, _UnknownHotelError):
                        delta.unknown_hotels = 1
                    await self._record_hotel_issue(job, line_number, raw_line, exc)
                    self._apply_delta(job, delta)
                except Exception as exc:
                    job.hotels_failed += 1
                    job.failed = job.hotels_failed
                    await self._record_hotel_issue(
                        job, line_number, raw_line, exc, "SENTIMENT_PERSISTENCE_ERROR"
                    )
                    delta = ImportCounterDelta()
                else:
                    self._apply_delta(job, delta)
                job.failed = job.hotels_failed
                job.progress = min(99.0, handle.tell() / file_size * 100.0)
                job.checkpoint = {"line": line_number}
                if line_number % self._batch_size == 0:
                    await self._session.commit()

    async def _process_hotel(
        self, job: ImportJob, line_number: int, record: HotelSentimentRecord
    ) -> ImportCounterDelta:
        source_hotel_id = str(record.hotel_id)
        mapping = await self._hotels.source_mapping(job.source_id, source_hotel_id)
        if mapping is None:
            raise _UnknownHotelError(f"UNKNOWN_SENTIMENT_HOTEL: {source_hotel_id}")
        delta = ImportCounterDelta(hotels_matched=1)
        values: list[dict[str, Any]] = []
        seen_attributes: dict[tuple[str, str], str] = {}
        attributes = record.attributes
        for raw_category_key, raw_category_value in attributes.items():
            if not isinstance(raw_category_key, str) or not isinstance(raw_category_value, dict):
                delta.attributes_failed += 1
                await self._record_attribute_issue(
                    job,
                    line_number,
                    source_hotel_id,
                    "INVALID_ATTRIBUTE_CATEGORY",
                    {"raw_category_key": raw_category_key},
                )
                continue
            try:
                category_key = normalize_taxonomy_key(raw_category_key)
            except ImportValidationError as exc:
                delta.attributes_failed += 1
                await self._record_attribute_issue(
                    job,
                    line_number,
                    source_hotel_id,
                    str(exc),
                    {"raw_category_key": raw_category_key},
                )
                continue
            for raw_attribute_key, raw_attribute_value in raw_category_value.items():
                delta.attributes_seen += 1
                if not isinstance(raw_attribute_key, str) or not isinstance(
                    raw_attribute_value, dict
                ):
                    delta.attributes_failed += 1
                    await self._record_attribute_issue(
                        job,
                        line_number,
                        source_hotel_id,
                        "INVALID_ATTRIBUTE_VALUE",
                        {
                            "raw_category_key": raw_category_key,
                            "raw_attribute_key": raw_attribute_key,
                        },
                    )
                    continue
                try:
                    parsed = AttributeSentimentValue.from_payload(raw_attribute_value)
                except ImportValidationError as exc:
                    delta.attributes_failed += 1
                    await self._record_attribute_issue(
                        job,
                        line_number,
                        source_hotel_id,
                        str(exc),
                        {
                            "raw_category_key": raw_category_key,
                            "raw_attribute_key": raw_attribute_key,
                        },
                    )
                    continue
                if parsed.total_mentions == 0:
                    delta.attributes_skipped_zero_mentions += 1
                    delta.skipped += 1
                    continue
                try:
                    attribute_key = normalize_taxonomy_key(raw_attribute_key)
                except ImportValidationError as exc:
                    delta.attributes_failed += 1
                    await self._record_attribute_issue(
                        job,
                        line_number,
                        source_hotel_id,
                        str(exc),
                        {
                            "raw_category_key": raw_category_key,
                            "raw_attribute_key": raw_attribute_key,
                        },
                    )
                    continue
                duplicate_key = (category_key, attribute_key)
                if duplicate_key in seen_attributes:
                    await self._record_attribute_issue(
                        job,
                        line_number,
                        source_hotel_id,
                        "DUPLICATE_NORMALIZED_ATTRIBUTE",
                        {
                            "raw_category_key": raw_category_key,
                            "category_key": category_key,
                            "winning_raw_attribute_key": seen_attributes[duplicate_key],
                            "conflicting_raw_attribute_key": raw_attribute_key,
                        },
                    )
                    continue
                seen_attributes[duplicate_key] = raw_attribute_key
                try:
                    resolution = await self._aggregates.resolve_attribute(
                        raw_category_key, raw_attribute_key
                    )
                except AmbiguousAttributeAlias as exc:
                    delta.attributes_failed += 1
                    await self._record_attribute_issue(
                        job,
                        line_number,
                        source_hotel_id,
                        "AMBIGUOUS_ATTRIBUTE_ALIAS",
                        {
                            "raw_category_key": raw_category_key,
                            "raw_attribute_key": raw_attribute_key,
                            "attribute_ids": [str(item) for item in exc.attribute_ids],
                        },
                    )
                    continue
                except ImportValidationError as exc:
                    delta.attributes_failed += 1
                    await self._record_attribute_issue(
                        job,
                        line_number,
                        source_hotel_id,
                        str(exc),
                        {
                            "raw_category_key": raw_category_key,
                            "raw_attribute_key": raw_attribute_key,
                        },
                    )
                    continue
                delta.auto_created_attributes += int(resolution.auto_created)
                values.append(
                    {
                        "id": uuid.uuid4(),
                        "hotel_id": mapping.hotel_id,
                        "attribute_id": resolution.attribute_id,
                        "source_id": job.source_id,
                        "category_name": category_key,
                        "raw_attribute_key": raw_attribute_key,
                        "sentiment": parsed.sentiment.value,
                        "positive_mentions": parsed.positive_mentions,
                        "negative_mentions": parsed.negative_mentions,
                        "total_mentions": parsed.total_mentions,
                        "reviews_analyzed": record.reviews_analyzed,
                        "analysis_mode": record.analysis_mode,
                        "analysis_version": record.analysis_version,
                        "review_window": record.review_window,
                        "aggregate_score_0_5": aggregate_score_0_5(
                            parsed.positive_mentions,
                            parsed.negative_mentions,
                            parsed.total_mentions,
                        ),
                        "scoring_source": "aggregate_sentiment",
                        "metadata_payload": {
                            "raw_category_key": raw_category_key,
                            "raw_hotel_id": source_hotel_id,
                            "has_recent_reviews": record.has_recent_reviews,
                            "import_job_id": str(job.id),
                        },
                    }
                )
        result = await self._aggregates.upsert_many(values)
        propagated = await self._scoring.propagate(result.rows)
        delta.attributes_processed += len(result.rows)
        delta.attributes_inserted += result.inserted
        delta.attributes_updated += result.updated
        delta.score_updates_applied += propagated.applied
        delta.score_updates_skipped_precedence += propagated.skipped_precedence
        return delta

    def _apply_delta(self, job: ImportJob, delta: ImportCounterDelta) -> None:
        for field in (
            "hotels_matched",
            "hotels_failed",
            "attributes_seen",
            "attributes_processed",
            "attributes_inserted",
            "attributes_updated",
            "attributes_skipped_zero_mentions",
            "attributes_failed",
            "score_updates_applied",
            "score_updates_skipped_precedence",
            "unknown_hotels",
            "auto_created_attributes",
            "skipped",
        ):
            setattr(job, field, getattr(job, field) + getattr(delta, field))
        job.inserted = job.attributes_inserted
        job.updated = job.attributes_updated

    async def _record_hotel_issue(
        self,
        job: ImportJob,
        line_number: int,
        raw_line: str,
        error: Exception,
        issue_type: str | None = None,
    ) -> None:
        source_entity_id: str | None = None
        try:
            payload = json.loads(raw_line)
            if isinstance(payload, dict) and payload.get("hotel_id") is not None:
                source_entity_id = str(payload["hotel_id"])
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        await self._quality.add(
            issue_type or self._quality_type(error),
            "ERROR",
            {
                "line": line_number,
                "error": str(error),
                "raw": raw_line[:4000],
            },
            source_id=job.source_id,
            import_job_id=job.id,
            entity_type="SENTIMENT_ANALYSIS",
            source_entity_id=source_entity_id,
        )

    async def _record_attribute_issue(
        self,
        job: ImportJob,
        line_number: int,
        source_hotel_id: str,
        issue_type: str,
        details: dict[str, Any],
    ) -> None:
        await self._quality.add(
            issue_type,
            "ERROR",
            {"line": line_number, **details},
            source_id=job.source_id,
            import_job_id=job.id,
            entity_type="SENTIMENT_ANALYSIS",
            source_entity_id=source_hotel_id,
        )

    async def _mark_failed(self, job_id: uuid.UUID, error: Exception) -> None:
        await self._session.rollback()
        job = await self._jobs.get(job_id)
        if job is None:
            return
        job.status = JobStatus.FAILED.value
        job.error = str(error)[:2000]
        await self._quality.add(
            "IMPORT_JOB_ERROR",
            "ERROR",
            {"error": str(error), "scope": "sentiment_import_job"},
            source_id=job.source_id,
            import_job_id=job.id,
            entity_type="SENTIMENT_ANALYSIS",
        )
        await self._session.commit()

    @staticmethod
    def _quality_type(error: Exception) -> str:
        if isinstance(error, _UnknownHotelError):
            return "UNKNOWN_SENTIMENT_HOTEL"
        if isinstance(error, json.JSONDecodeError):
            return "MALFORMED_SENTIMENT_JSON"
        if isinstance(error, ImportValidationError):
            return "INVALID_SENTIMENT_RECORD"
        return "SENTIMENT_PERSISTENCE_ERROR"

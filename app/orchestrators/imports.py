"""Chunked idempotent hotel and review CSV workflows."""

import ast
import csv
import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from geoalchemy2.elements import WKTElement
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.logging import get_logger
from app.domain.scoring import normalize_rating
from app.enums import JobStatus
from app.exceptions import ImportValidationError
from app.integrations.storage.base import ObjectStorage
from app.models import ImportJob
from app.repositories.catalog import HotelRepository, ReviewRepository
from app.repositories.locations import LocationRepository
from app.repositories.operations import DataQualityRepository, ImportJobRepository

HOTEL_COLUMNS = {"locationId", "name", "city"}
REVIEW_COLUMNS = {"hotel_location_id", "review_id", "review_rating"}


class CSVImportOrchestrator:
    """Process staged CSVs in resumable batches with row-level quality records."""

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
        self._reviews = ReviewRepository(session)
        self._locations = LocationRepository(session)
        self._quality = DataQualityRepository(session)

    async def run(self, job_id: uuid.UUID) -> None:
        """Resume and execute one pending or failed import job."""
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
            if job.import_type == "HOTELS":
                await self._run_hotels(job, path)
            else:
                await self._run_reviews(job, path)
            job.status = JobStatus.PARTIAL.value if job.failed else JobStatus.COMPLETED.value
            job.progress = 100
            job.completed_at = datetime.now(UTC)
            await self._session.commit()
            get_logger().info(
                f"{job.import_type.lower()}_import_completed",
                job_id=str(job.id),
                inserted=job.inserted,
                updated=job.updated,
                failed=job.failed,
            )
        except Exception as exc:
            await self._session.rollback()
            fresh_job = await self._jobs.get(job_id)
            if fresh_job:
                fresh_job.status = JobStatus.FAILED.value
                fresh_job.error = str(exc)[:2000]
                await self._quality.add(
                    self._quality_type(exc),
                    "ERROR",
                    {"error": str(exc), "scope": "import_job"},
                    source_id=fresh_job.source_id,
                    import_job_id=fresh_job.id,
                    entity_type=fresh_job.import_type,
                )
                await self._session.commit()
            raise
        finally:
            await self._storage.release(path)

    async def _run_hotels(self, job: ImportJob, path: Path) -> None:
        if job.country_id is None:
            raise ImportValidationError("MISSING_COUNTRY: country_id is required")
        if job.region_id is None:
            raise ImportValidationError("MISSING_REGION: region_id is required")
        start_row = int((job.checkpoint or {}).get("row", 0))
        file_size = max(path.stat().st_size, 1)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            self._require_columns(reader.fieldnames, HOTEL_COLUMNS)
            for row_number, row in enumerate(reader, start=1):
                if row_number <= start_row:
                    continue
                job.records_read += 1
                try:
                    async with self._session.begin_nested():
                        inserted = await self._hotel_row(job, row)
                    job.inserted += int(inserted)
                    job.updated += int(not inserted)
                except Exception as exc:
                    job.failed += 1
                    await self._record_issue(job, row_number, row, exc, "HOTEL", "locationId")
                job.progress = min(99.0, handle.buffer.tell() / file_size * 100.0)
                await self._checkpoint(job, row_number)

    async def _hotel_row(self, job: ImportJob, row: dict[str, str]) -> bool:
        source_hotel_id = self._required_identifier(row, "locationId")
        name = self._required_text(row, "name")
        city_name = self._required_text(row, "city")
        latitude = self._coordinate(row.get("latitude"), -90, 90, "latitude")
        longitude = self._coordinate(row.get("longitude"), -180, 180, "longitude")
        assert job.region_id is not None
        city = await self._locations.find_city(job.region_id, city_name)
        if city is None:
            raise ImportValidationError(f"MISSING_CITY: {city_name}")
        now = datetime.now(UTC)
        hotel_id, inserted = await self._hotels.upsert_source_hotel(
            {
                "name": name,
                "country_id": job.country_id,
                "region_id": job.region_id,
                "city_id": city.id,
                "hotel_type": self._optional_text(row.get("hotel_type")),
                "address": self._optional_text(row.get("address")),
                "postal_code": self._optional_text(row.get("postal_code")),
                "telephone": self._optional_text(row.get("telephone")),
                "latitude": latitude,
                "longitude": longitude,
                "geo_location": (
                    WKTElement(f"POINT({longitude} {latitude})", srid=4326)
                    if latitude is not None and longitude is not None
                    else None
                ),
                "is_active": True,
            },
            job.source_id,
            source_hotel_id,
            {
                "source_url": self._optional_text(row.get("tripadvisor_url")),
                "source_rating": self._optional_float(row.get("rating")),
                "source_review_count": self._optional_int(row.get("review_count")),
                "source_rank": self._optional_int(row.get("rank")),
                "source_rank_text": self._optional_text(row.get("rank_text")),
                "raw_metadata": {"import_file": job.file_name},
                "first_seen_at": now,
                "last_seen_at": now,
                "is_active": True,
            },
        )
        image_url = self._optional_text(row.get("image_url"))
        if image_url:
            await self._hotels.upsert_image(
                hotel_id,
                job.source_id,
                image_url,
                position=0,
                is_primary=True,
                created_at=now,
            )
        return inserted

    async def _run_reviews(self, job: ImportJob, path: Path) -> None:
        start_row = int((job.checkpoint or {}).get("row", 0))
        file_size = max(path.stat().st_size, 1)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            self._require_columns(reader.fieldnames, REVIEW_COLUMNS)
            for row_number, row in enumerate(reader, start=1):
                if row_number <= start_row:
                    continue
                job.records_read += 1
                try:
                    async with self._session.begin_nested():
                        inserted = await self._review_row(job, row)
                    job.inserted += int(inserted)
                    job.updated += int(not inserted)
                except Exception as exc:
                    job.failed += 1
                    await self._record_issue(job, row_number, row, exc, "REVIEW", "review_id")
                job.progress = min(99.0, handle.buffer.tell() / file_size * 100.0)
                await self._checkpoint(job, row_number)

    async def _review_row(self, job: ImportJob, row: dict[str, str]) -> bool:
        source_hotel_id = self._required_identifier(row, "hotel_location_id")
        mapping = await self._hotels.source_mapping(job.source_id, source_hotel_id)
        if mapping is None:
            raise ImportValidationError(f"ORPHAN_REVIEW: {source_hotel_id}")
        source_review_id = self._required_identifier(row, "review_id")
        source_rating = self._required_float(row.get("review_rating"), "review_rating")
        source_scale = self._optional_float(row.get("rating_scale")) or 5.0
        if source_rating < 0 or source_rating > source_scale:
            raise ImportValidationError("INVALID_REVIEW_RATING")
        review_id, inserted = await self._reviews.upsert_review(
            {
                "hotel_id": mapping.hotel_id,
                "source_id": job.source_id,
                "source_review_id": source_review_id,
                "source_rating": source_rating,
                "source_rating_scale": source_scale,
                "normalized_rating_5": normalize_rating(source_rating, source_scale),
                "title": self._optional_text(row.get("review_title")),
                "review_text": self._optional_text(row.get("review_text")),
                "review_date": self._parse_date(row.get("review_date")),
                "reviewer_name": self._optional_text(row.get("user")),
                "trip_type": self._optional_text(row.get("trip_type")),
                "language": self._optional_text(row.get("language")),
                "source_url": self._optional_text(row.get("source_url")),
                "scraped_at": datetime.now(UTC),
            }
        )
        for position, url in enumerate(self._image_urls(row.get("images"))):
            await self._reviews.upsert_image(
                review_id,
                url,
                position,
                datetime.now(UTC),
            )
        return inserted

    async def _record_issue(
        self,
        job: ImportJob,
        row_number: int,
        row: dict[str, str],
        error: Exception,
        entity_type: str,
        identity_field: str,
    ) -> None:
        await self._quality.add(
            self._quality_type(error),
            "ERROR",
            {"row": row_number, "error": str(error), "raw": row},
            source_id=job.source_id,
            import_job_id=job.id,
            entity_type=entity_type,
            source_entity_id=row.get(identity_field),
        )
        if self._quality_type(error) == "ORPHAN_REVIEW":
            for issue_type in (
                "UNKNOWN_HOTEL_LOCATION_ID",
                "MISSING_SOURCE_MAPPING",
            ):
                await self._quality.add(
                    issue_type,
                    "ERROR",
                    {"row": row_number, "error": str(error), "raw": row},
                    source_id=job.source_id,
                    import_job_id=job.id,
                    entity_type=entity_type,
                    source_entity_id=row.get(identity_field),
                )

    async def _checkpoint(self, job: ImportJob, row_number: int) -> None:
        job.checkpoint = {"row": row_number}
        if row_number % self._batch_size == 0:
            await self._session.commit()

    @staticmethod
    def _require_columns(actual: list[str] | None, required: set[str]) -> None:
        missing = required - set(actual or [])
        if missing:
            raise ImportValidationError(f"Missing CSV columns: {sorted(missing)}")

    @staticmethod
    def _required_text(row: dict[str, str], key: str) -> str:
        value = CSVImportOrchestrator._optional_text(row.get(key))
        if value is None:
            raise ImportValidationError(f"Missing required field: {key}")
        return value

    @staticmethod
    def _required_identifier(row: dict[str, str], key: str) -> str:
        """Validate an upstream identifier without transforming any character."""
        value = row.get(key)
        if value is None or not value.strip():
            raise ImportValidationError(f"Missing required field: {key}")
        return value

    @staticmethod
    def _optional_text(value: str | None) -> str | None:
        stripped = value.strip() if value else ""
        return stripped or None

    @staticmethod
    def _required_float(value: str | None, key: str) -> float:
        parsed = CSVImportOrchestrator._optional_float(value)
        if parsed is None:
            raise ImportValidationError(f"Missing or invalid numeric field: {key}")
        return parsed

    @staticmethod
    def _optional_float(value: str | None) -> float | None:
        try:
            return float(value) if value and value.strip() else None
        except ValueError as exc:
            raise ImportValidationError(f"Invalid numeric value: {value}") from exc

    @staticmethod
    def _optional_int(value: str | None) -> int | None:
        parsed = CSVImportOrchestrator._optional_float(value)
        return int(parsed) if parsed is not None else None

    @staticmethod
    def _coordinate(
        value: str | None, low: float, high: float, name: str
    ) -> float | None:
        parsed = CSVImportOrchestrator._optional_float(value)
        if parsed is None:
            return None
        if not low <= parsed <= high:
            raise ImportValidationError(f"INVALID_{name.upper()}")
        return parsed

    @staticmethod
    def _parse_date(value: str | None) -> date | None:
        if not value or not value.strip():
            return None
        raw = value.strip()
        formats = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S")
        for date_format in formats:
            try:
                return datetime.strptime(raw, date_format).date()
            except ValueError:
                continue
        raise ImportValidationError("INVALID_DATE")

    @staticmethod
    def _image_urls(value: str | None) -> list[str]:
        if not value or not value.strip():
            return []
        raw = value.strip()
        parsed: Any = None
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(raw)
                break
            except (ValueError, SyntaxError, json.JSONDecodeError):
                continue
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in raw.split(",") if item.strip()]

    @staticmethod
    def _quality_type(error: Exception) -> str:
        message = str(error)
        for known in (
            "ORPHAN_REVIEW",
            "INVALID_LATITUDE",
            "INVALID_LONGITUDE",
            "INVALID_REVIEW_RATING",
            "INVALID_DATE",
            "MISSING_CITY",
            "MISSING_REGION",
        ):
            if known in message:
                return known
        return "IMPORT_VALIDATION_ERROR"

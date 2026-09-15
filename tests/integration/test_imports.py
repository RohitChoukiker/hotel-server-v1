"""Real-database CSV idempotency, checkpoint and data-quality tests."""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.database import create_engine, create_session_factory
from app.enums import ImportType
from app.integrations.storage.local import LocalObjectStorage
from app.models import City, DataQualityIssue, HotelSourceMapping, Review
from app.orchestrators.imports import CSVImportOrchestrator
from app.services.imports import ImportService
from scripts.seed_reference_data import seed, stable_id

pytestmark = pytest.mark.integration


async def _chunks(content: bytes) -> AsyncIterator[bytes]:
    yield content


@pytest.mark.asyncio
async def test_imports_resume_deduplicate_and_report_orphans(tmp_path: object) -> None:
    await seed()
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    storage = LocalObjectStorage(tmp_path)  # type: ignore[arg-type]
    country_id = stable_id("country:IN")
    region_id = stable_id("region:IN:GA")
    city_name = f"Import City {uuid.uuid4()}"
    source_hotel_id = f"source-hotel-{uuid.uuid4()}"
    async with factory() as session:
        session.add(
            City(
                region_id=region_id,
                name=city_name,
                latitude=15.4,
                longitude=73.8,
                timezone="Asia/Kolkata",
                is_active=True,
            )
        )
        await session.commit()

        hotel_csv = (
            "locationId,name,city,latitude,longitude,rating\n"
            f"{source_hotel_id},UTF-8 Hôtel,{city_name},15.4,73.8,4.5\n"
        ).encode("utf-8")
        first_job = await ImportService(session, storage).create_job(
            ImportType.HOTELS,
            "TRIPADVISOR",
            "hotels.csv",
            _chunks(hotel_csv),
            country_id,
            region_id,
        )
        await CSVImportOrchestrator(session, storage, 100).run(first_job.id)
        assert first_job.inserted == 1
        assert first_job.checkpoint == {"row": 1}

        second_job = await ImportService(session, storage).create_job(
            ImportType.HOTELS,
            "TRIPADVISOR",
            "hotels.csv",
            _chunks(hotel_csv),
            country_id,
            region_id,
        )
        await CSVImportOrchestrator(session, storage, 100).run(second_job.id)
        assert second_job.inserted == 0
        assert second_job.updated == 1
        mapping_count = await session.scalar(
            select(func.count())
            .select_from(HotelSourceMapping)
            .where(HotelSourceMapping.source_hotel_id == source_hotel_id)
        )
        assert mapping_count == 1

        valid_review_id = f"review-{uuid.uuid4()}"
        review_csv = (
            "hotel_location_id,review_id,review_rating,review_text,review_date\n"
            f"{source_hotel_id},{valid_review_id},4,Great cleanliness,2026-09-11\n"
            f"unknown-{uuid.uuid4()},orphan-{uuid.uuid4()},5,Great,2026-09-11\n"
        ).encode("utf-8")
        review_job = await ImportService(session, storage).create_job(
            ImportType.REVIEWS,
            "TRIPADVISOR",
            "reviews.csv",
            _chunks(review_csv),
            None,
            None,
        )
        await CSVImportOrchestrator(session, storage, 1).run(review_job.id)
        assert review_job.inserted == 1
        assert review_job.failed == 1
        assert review_job.status == "PARTIAL"
        review_count = await session.scalar(
            select(func.count())
            .select_from(Review)
            .where(Review.source_review_id == valid_review_id)
        )
        assert review_count == 1
        issue_types = set(
            (
                await session.scalars(
                    select(DataQualityIssue.issue_type).where(
                        DataQualityIssue.import_job_id == review_job.id
                    )
                )
            ).all()
        )
        assert {
            "ORPHAN_REVIEW",
            "UNKNOWN_HOTEL_LOCATION_ID",
            "MISSING_SOURCE_MAPPING",
        } <= issue_types
    await engine.dispose()

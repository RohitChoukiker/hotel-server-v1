"""Real-database CSV idempotency, checkpoint and data-quality tests."""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.database import create_engine, create_session_factory
from app.enums import ImportType
from app.integrations.storage.local import LocalObjectStorage
from app.models import City, DataQualityIssue, Hotel, HotelSourceMapping, Review
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


@pytest.mark.asyncio
async def test_hotel_import_allows_optional_coordinates_and_rejects_invalid_values(
    tmp_path: object,
) -> None:
    await seed()
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    storage = LocalObjectStorage(tmp_path)  # type: ignore[arg-type]
    country_id = stable_id("country:IN")
    region_id = stable_id("region:IN:GA")
    city_name = f"Coordinate City {uuid.uuid4()}"
    row_ids = {
        name: f"{name}-{uuid.uuid4()}"
        for name in (
            "both-missing",
            "latitude-missing",
            "longitude-missing",
            "valid",
            "malformed",
            "latitude-out-of-range",
            "longitude-out-of-range",
            "missing-city",
        )
    }
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
            "locationId,name,city,latitude,longitude\n"
            f"{row_ids['both-missing']},Both Missing,{city_name},,\n"
            f"{row_ids['latitude-missing']},Latitude Missing,{city_name},,92.71\n"
            f"{row_ids['longitude-missing']},Longitude Missing,{city_name},23.72,\n"
            f"{row_ids['valid']},Valid Coordinates,{city_name},23.72,92.71\n"
            f"{row_ids['malformed']},Malformed Coordinates,{city_name},abc,92.71\n"
            f"{row_ids['latitude-out-of-range']},Bad Latitude,{city_name},999,92.71\n"
            f"{row_ids['longitude-out-of-range']},Bad Longitude,{city_name},23.72,999\n"
            f"{row_ids['missing-city']},Missing City,Unknown City,,\n"
        ).encode()
        job = await ImportService(session, storage).create_job(
            ImportType.HOTELS,
            "TRIPADVISOR",
            "optional-coordinates.csv",
            _chunks(hotel_csv),
            country_id,
            region_id,
        )
        await CSVImportOrchestrator(session, storage, 100).run(job.id)

        assert job.inserted == 4
        assert job.failed == 4
        assert job.status == "PARTIAL"

        imported = {
            row.source_hotel_id: row
            for row in (
                await session.execute(
                    select(
                        HotelSourceMapping.source_hotel_id,
                        Hotel.latitude,
                        Hotel.longitude,
                        Hotel.geo_location,
                    )
                    .join(Hotel, Hotel.id == HotelSourceMapping.hotel_id)
                    .where(HotelSourceMapping.source_hotel_id.in_(row_ids.values()))
                )
            ).all()
        }
        both_missing = imported[row_ids["both-missing"]]
        assert both_missing.latitude is None
        assert both_missing.longitude is None
        assert both_missing.geo_location is None

        latitude_missing = imported[row_ids["latitude-missing"]]
        assert latitude_missing.latitude is None
        assert float(latitude_missing.longitude) == 92.71
        assert latitude_missing.geo_location is None

        longitude_missing = imported[row_ids["longitude-missing"]]
        assert float(longitude_missing.latitude) == 23.72
        assert longitude_missing.longitude is None
        assert longitude_missing.geo_location is None

        valid = imported[row_ids["valid"]]
        assert float(valid.latitude) == 23.72
        assert float(valid.longitude) == 92.71
        assert valid.geo_location is not None

        assert row_ids["malformed"] not in imported
        assert row_ids["latitude-out-of-range"] not in imported
        assert row_ids["longitude-out-of-range"] not in imported
        assert row_ids["missing-city"] not in imported

        issue_types = set(
            (
                await session.scalars(
                    select(DataQualityIssue.issue_type).where(
                        DataQualityIssue.import_job_id == job.id
                    )
                )
            ).all()
        )
        assert {
            "IMPORT_VALIDATION_ERROR",
            "INVALID_LATITUDE",
            "INVALID_LONGITUDE",
            "MISSING_CITY",
        } <= issue_types
    await engine.dispose()

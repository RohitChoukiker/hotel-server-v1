"""Real-database CSV idempotency, checkpoint and data-quality tests."""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

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


class _TrackingCache:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, *keys: str) -> None:
        self.deleted.extend(keys)


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
    auto_created_city_name = f"Auto Created City {uuid.uuid4()}"
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
    cache = _TrackingCache()
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
            f"{row_ids['missing-city']},Missing City,{auto_created_city_name},,\n"
        ).encode()
        job = await ImportService(session, storage).create_job(
            ImportType.HOTELS,
            "TRIPADVISOR",
            "optional-coordinates.csv",
            _chunks(hotel_csv),
            country_id,
            region_id,
        )
        await CSVImportOrchestrator(session, storage, 100, cache=cache).run(job.id)

        assert job.inserted == 5
        assert job.failed == 3
        assert job.status == "PARTIAL"
        assert cache.deleted == [f"locations:region:{region_id}:cities:v1"]

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
        auto_created = imported[row_ids["missing-city"]]
        assert auto_created.latitude is None
        assert auto_created.longitude is None
        assert auto_created.geo_location is None

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
            "AUTO_CREATED_CITY",
        } <= issue_types

        no_coordinate_columns_id = f"no-coordinate-columns-{uuid.uuid4()}"
        no_coordinate_columns_csv = (
            "locationId,name,city\n"
            f"{no_coordinate_columns_id},No Coordinate Columns,{city_name}\n"
        ).encode()
        no_coordinate_columns_job = await ImportService(
            session, storage
        ).create_job(
            ImportType.HOTELS,
            "TRIPADVISOR",
            "no-coordinate-columns.csv",
            _chunks(no_coordinate_columns_csv),
            country_id,
            region_id,
        )
        await CSVImportOrchestrator(session, storage, 100).run(
            no_coordinate_columns_job.id
        )

        imported_without_columns = (
            await session.execute(
                select(Hotel.latitude, Hotel.longitude, Hotel.geo_location)
                .join(HotelSourceMapping, HotelSourceMapping.hotel_id == Hotel.id)
                .where(
                    HotelSourceMapping.source_hotel_id == no_coordinate_columns_id
                )
            )
        ).one()
        assert no_coordinate_columns_job.inserted == 1
        assert imported_without_columns.latitude is None
        assert imported_without_columns.longitude is None
        assert imported_without_columns.geo_location is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_hotel_import_resolves_or_creates_cities_without_changing_row_counters(
    tmp_path: object,
) -> None:
    """Catch missing-city failures, case duplicates, and counter regressions."""
    await seed()
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    storage = LocalObjectStorage(tmp_path)  # type: ignore[arg-type]
    country_id = stable_id("country:IN")
    region_id = stable_id("region:IN:GA")
    existing_name = f"Existing City {uuid.uuid4()}"
    new_name = f"New City {uuid.uuid4()}"
    row_ids = {
        "existing": f"existing-{uuid.uuid4()}",
        "new-one": f"new-one-{uuid.uuid4()}",
        "new-two": f"new-two-{uuid.uuid4()}",
        "blank": f"blank-{uuid.uuid4()}",
    }

    async with factory() as session:
        existing_city = City(
            region_id=region_id,
            name=existing_name,
            latitude=15.4,
            longitude=73.8,
            timezone="Asia/Kolkata",
            is_active=True,
        )
        session.add(existing_city)
        await session.commit()
        existing_city_id = existing_city.id

        hotel_csv = (
            "locationId,name,city\n"
            f"{row_ids['existing']},Existing Hotel,  {existing_name.swapcase()}  \n"
            f"{row_ids['new-one']},New Hotel One,  {new_name}  \n"
            f"{row_ids['new-two']},New Hotel Two,{new_name.swapcase()}\n"
            f"{row_ids['blank']},Blank City Hotel,   \n"
        ).encode()
        job = await ImportService(session, storage).create_job(
            ImportType.HOTELS,
            "TRIPADVISOR",
            "city-resolution.csv",
            _chunks(hotel_csv),
            country_id,
            region_id,
        )
        await CSVImportOrchestrator(session, storage, 100).run(job.id)

        assert job.records_read == 4
        assert job.inserted == 3
        assert job.updated == 0
        assert job.failed == 1
        assert job.status == "PARTIAL"

        imported_city_ids = dict(
            (
                await session.execute(
                    select(HotelSourceMapping.source_hotel_id, Hotel.city_id)
                    .join(Hotel, Hotel.id == HotelSourceMapping.hotel_id)
                    .where(HotelSourceMapping.source_hotel_id.in_(row_ids.values()))
                )
            ).all()
        )
        assert imported_city_ids[row_ids["existing"]] == existing_city_id
        assert row_ids["blank"] not in imported_city_ids

        normalized_new_cities = list(
            (
                await session.scalars(
                    select(City).where(
                        City.region_id == region_id,
                        func.lower(func.btrim(City.name)) == new_name.casefold(),
                    )
                )
            ).all()
        )
        assert len(normalized_new_cities) == 1
        created_city = normalized_new_cities[0]
        assert created_city.name == new_name
        assert created_city.latitude is None
        assert created_city.longitude is None
        assert created_city.timezone is None
        assert created_city.is_active is True
        assert imported_city_ids[row_ids["new-one"]] == created_city.id
        assert imported_city_ids[row_ids["new-two"]] == created_city.id

        existing_city_count = await session.scalar(
            select(func.count())
            .select_from(City)
            .where(
                City.region_id == region_id,
                func.lower(func.btrim(City.name)) == existing_name.casefold(),
            )
        )
        assert existing_city_count == 1

        auto_created_issue = (
            await session.scalars(
                select(DataQualityIssue).where(
                    DataQualityIssue.import_job_id == job.id,
                    DataQualityIssue.issue_type == "AUTO_CREATED_CITY",
                )
            )
        ).one()
        assert auto_created_issue.severity == "INFO"
        assert auto_created_issue.details == {
            "city_name": new_name,
            "region_id": str(region_id),
        }

        blank_city_issue = (
            await session.scalars(
                select(DataQualityIssue).where(
                    DataQualityIssue.import_job_id == job.id,
                    DataQualityIssue.source_entity_id == row_ids["blank"],
                )
            )
        ).one()
        assert blank_city_issue.issue_type == "IMPORT_VALIDATION_ERROR"
        assert "Missing required field: city" in blank_city_issue.details["error"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_hotel_import_completes_when_every_city_must_be_created(
    tmp_path: object,
) -> None:
    """Catch regressions that still classify absent valid cities as row failures."""
    await seed()
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    storage = LocalObjectStorage(tmp_path)  # type: ignore[arg-type]
    country_id = stable_id("country:IN")
    region_id = stable_id("region:IN:GA")
    first_city = f"First Missing City {uuid.uuid4()}"
    second_city = f"Second Missing City {uuid.uuid4()}"
    first_hotel_id = f"first-missing-{uuid.uuid4()}"
    second_hotel_id = f"second-missing-{uuid.uuid4()}"

    async with factory() as session:
        hotel_csv = (
            "locationId,name,city\n"
            f"{first_hotel_id},First Missing Hotel,{first_city}\n"
            f"{second_hotel_id},Second Missing Hotel,{second_city}\n"
        ).encode()
        job = await ImportService(session, storage).create_job(
            ImportType.HOTELS,
            "TRIPADVISOR",
            "all-new-cities.csv",
            _chunks(hotel_csv),
            country_id,
            region_id,
        )
        await CSVImportOrchestrator(session, storage, 100).run(job.id)

        assert job.records_read == 2
        assert job.inserted == 2
        assert job.updated == 0
        assert job.failed == 0
        assert job.status == "COMPLETED"
        imported_count = await session.scalar(
            select(func.count())
            .select_from(HotelSourceMapping)
            .where(
                HotelSourceMapping.source_hotel_id.in_(
                    (first_hotel_id, second_hotel_id)
                )
            )
        )
        assert imported_count == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_city_names_are_unique_per_region_ignoring_case_and_whitespace() -> None:
    """Catch schema regressions that allow concurrent normalized duplicates."""
    await seed()
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    region_id = stable_id("region:IN:GA")
    city_name = f"Constraint City {uuid.uuid4()}"

    async with factory() as session:
        try:
            session.add(City(region_id=region_id, name=city_name, is_active=True))
            session.add(
                City(
                    region_id=region_id,
                    name=f"  {city_name.swapcase()}  ",
                    is_active=True,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
        finally:
            await session.rollback()
    await engine.dispose()

"""Empty-database migration and reference-data integration checks."""

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.database import create_engine
from scripts.seed_reference_data import seed

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_postgis_and_complete_schema() -> None:
    engine = create_engine(get_settings())
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT PostGIS_Version()"))
            tables = await connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
            names = {row[0] for row in tables}
        assert version
        assert {"hotels", "reviews", "hotel_attribute_scores", "recommendation_runs"} <= names
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_seed_is_idempotent_and_complete() -> None:
    await seed()
    await seed()
    engine = create_engine(get_settings())
    try:
        async with engine.connect() as connection:
            states = await connection.scalar(
                text("SELECT count(*) FROM regions WHERE region_type='STATE'")
            )
            territories = await connection.scalar(
                text("SELECT count(*) FROM regions WHERE region_type='UNION_TERRITORY'")
            )
            attributes = await connection.scalar(text("SELECT count(*) FROM attributes"))
            questions = await connection.scalar(text("SELECT count(*) FROM onboarding_questions"))
        assert states == 28
        assert territories == 8
        assert attributes == 24
        assert questions == 8
    finally:
        await engine.dispose()


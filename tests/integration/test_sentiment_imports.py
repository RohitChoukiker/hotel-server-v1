"""End-to-end persistence checks for precomputed sentiment JSONL imports."""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.database import create_engine, create_session_factory
from app.enums import ImportType
from app.integrations.storage.local import LocalObjectStorage
from app.models import (
    City,
    Hotel,
    HotelAttributeScore,
    HotelAttributeSentimentAggregate,
    HotelSourceMapping,
    NormalizationRun,
    ReviewAttributeMention,
)
from app.orchestrators.sentiment_imports import SentimentAnalysisImportOrchestrator
from app.services.imports import ImportService
from app.services.scoring import ScoreNormalizationService
from scripts.seed_reference_data import seed, stable_id

pytestmark = pytest.mark.integration


async def _chunks(content: bytes) -> AsyncIterator[bytes]:
    yield content


@pytest.mark.asyncio
async def test_real_jsonl_import_is_idempotent_and_respects_review_precedence(
    tmp_path: Path,
) -> None:
    await seed()
    engine = create_engine(get_settings())
    factory = create_session_factory(engine)
    storage = LocalObjectStorage(tmp_path)
    country_id = stable_id("country:IN")
    region_id = stable_id("region:IN:GA")
    source_id = stable_id("source:TRIPADVISOR")
    city = City(region_id=region_id, name=f"Sentiment City {uuid.uuid4()}", is_active=True)
    hotel = Hotel(
        name="Sentiment Verification Hotel",
        country_id=country_id,
        region_id=region_id,
        city_id=city.id,
        is_active=True,
    )
    source_hotel_id = f"real-source-{uuid.uuid4()}"
    now = datetime.now(UTC)
    async with factory() as session:
        session.add(city)
        await session.flush()
        hotel.city_id = city.id
        session.add(hotel)
        await session.flush()
        hotel_id = hotel.id
        session.add(
            HotelSourceMapping(
                hotel_id=hotel.id,
                source_id=source_id,
                source_hotel_id=source_hotel_id,
                first_seen_at=now,
                last_seen_at=now,
                is_active=True,
            )
        )
        await session.commit()
        baseline_mentions = await session.scalar(
            select(func.count()).select_from(ReviewAttributeMention)
        )
        baseline_aggregates = await session.scalar(
            select(func.count()).select_from(HotelAttributeSentimentAggregate)
        )

        jsonl = (
            json.dumps(
                {
                    "hotel_id": source_hotel_id,
                    "analysis_mode": "all_reviews",
                    "reviews_analyzed": 75,
                    "attributes": {
                        "location": {
                            "good_location": {
                                "sentiment": "positive",
                                "mentions": 13,
                                "positive_mentions": 13,
                                "negative_mentions": 0,
                            },
                            "unused_attribute": {
                                "sentiment": "positive",
                                "mentions": 0,
                                "positive_mentions": 0,
                                "negative_mentions": 0,
                            },
                        }
                    },
                }
            )
            + "\n"
        ).encode()
        job = await ImportService(session, storage).create_job(
            ImportType.SENTIMENT_ANALYSIS,
            "TRIPADVISOR",
            "real-sentiment.jsonl",
            _chunks(jsonl),
            None,
            None,
        )
        await SentimentAnalysisImportOrchestrator(session, storage, 1).run(job.id)
        assert job.status == "COMPLETED"
        assert job.records_read == job.hotels_read == 1
        assert job.hotels_matched == 1
        assert job.attributes_seen == 2
        assert job.attributes_processed == 1
        assert job.attributes_skipped_zero_mentions == 1
        assert job.attributes_inserted == job.inserted == 1
        assert job.score_updates_applied == 1

        aggregate = (
            await session.scalars(
                select(HotelAttributeSentimentAggregate).where(
                    HotelAttributeSentimentAggregate.hotel_id == hotel_id
                )
            )
        ).one()
        assert aggregate.aggregate_score_0_5 == 5
        assert aggregate.source_id == source_id
        assert aggregate.metadata_payload["raw_hotel_id"] == source_hotel_id
        assert (
            await session.scalar(select(func.count()).select_from(ReviewAttributeMention))
        ) == baseline_mentions
        score = (
            await session.scalars(
                select(HotelAttributeScore).where(HotelAttributeScore.hotel_id == hotel_id)
            )
        ).one()
        assert score.scoring_source == "aggregate_sentiment"
        assert (await session.scalar(select(func.count()).select_from(NormalizationRun))) >= 1

        score.scoring_source = "review_based"
        score.score_5 = 4.75
        score.mean = None
        score.standard_deviation = None
        score.z_score = None
        score.relative_score_5 = None
        await session.commit()
        second_job = await ImportService(session, storage).create_job(
            ImportType.SENTIMENT_ANALYSIS,
            "TRIPADVISOR",
            "real-sentiment.jsonl",
            _chunks(jsonl),
            None,
            None,
        )
        await SentimentAnalysisImportOrchestrator(session, storage, 1).run(second_job.id)
        assert second_job.status == "COMPLETED"
        assert second_job.attributes_inserted == 0
        assert second_job.attributes_updated == second_job.updated == 1
        assert second_job.score_updates_applied == 0
        assert second_job.score_updates_skipped_precedence == 1
        await session.refresh(score)
        assert score.scoring_source == "review_based"
        assert score.score_5 == 4.75
        assert score.mean is None
        assert score.standard_deviation is None
        assert score.z_score is None
        assert score.relative_score_5 is None
        assert (
            await session.scalar(select(func.count()).select_from(HotelAttributeSentimentAggregate))
        ) == baseline_aggregates + 1

        await ScoreNormalizationService(session).normalize(
            attribute_ids=[score.attribute_id],
        )
        await session.refresh(score)
        assert score.mean is not None
        assert score.standard_deviation is not None
        assert score.z_score is not None
        assert score.relative_score_5 is not None
    await engine.dispose()

"""Approved deterministic contribution scoring and bell-curve normalization."""

import math
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.logging import get_logger
from app.domain.scoring import (
    calculate_contribution,
    normalize_value,
    population_statistics,
    score_100,
)
from app.enums import ScopeType, Sentiment
from app.exceptions import ScoringError
from app.integrations.llm.base import StructuredTextInterpreter
from app.models import NormalizationRun, Review, ReviewAttributeMention
from app.repositories.catalog import AttributeRepository, ReviewRepository


class ScoringService:
    """Own background review extraction, aggregation, and normalization."""

    def __init__(
        self,
        session: AsyncSession,
        interpreter: StructuredTextInterpreter,
    ) -> None:
        self._session = session
        self._interpreter = interpreter
        self._reviews = ReviewRepository(session)
        self._attributes = AttributeRepository(session)

    async def process_reviews(self, limit: int = 500) -> int:
        """Extract validated mentions for unprocessed reviews outside live ranking."""
        algorithm = await self._attributes.active_algorithm()
        if algorithm is None:
            raise ScoringError("No active algorithm version")
        attribute_rows = await self._attributes.list_attributes()
        by_slug = {str(item["slug"]): item for item in attribute_rows}
        reviews = await self._reviews.unprocessed(algorithm.id, limit)
        # Close the read transaction before any external interpreter call.
        await self._session.commit()
        processed = 0
        failed = 0
        for review in reviews:
            try:
                outputs = await self._interpreter.extract_review_mentions(
                    review.review_text or "",
                    list(by_slug),
                )
            except Exception as exc:
                await self._reviews.mark_processing_status(
                    review.id,
                    algorithm.id,
                    "FAILED",
                    0,
                    str(exc)[:2000],
                )
                await self._session.commit()
                get_logger().error(
                    "scoring_failed",
                    review_id=str(review.id),
                    error_type=type(exc).__name__,
                )
                failed += 1
                continue
            mentions: list[ReviewAttributeMention] = []
            for output in outputs:
                attribute = by_slug.get(output.attribute_slug)
                if attribute is None:
                    continue
                mentions.append(self._mention_model(review, algorithm.id, attribute["id"], output))
            if mentions:
                await self._attributes.add_mentions(mentions)
            await self._reviews.mark_processing_status(
                review.id,
                algorithm.id,
                "COMPLETED",
                len(mentions),
            )
            processed += 1
            await self._session.commit()
        get_logger().info("review_processing_completed", processed=processed)
        if reviews and failed == len(reviews):
            raise ScoringError("All review extraction attempts failed")
        return processed

    async def recalculate_hotel(self, hotel_id: uuid.UUID) -> int:
        """Aggregate persisted mention contributions into precomputed hotel scores."""
        algorithm = await self._attributes.active_algorithm()
        if algorithm is None:
            raise ScoringError("No active algorithm version")
        rows = await self._attributes.contribution_rows(hotel_id, algorithm.id)
        now = datetime.now(UTC)
        for row in rows:
            raw = float(row["raw_score"])
            mentions = int(row["mention_count"])
            confidence = float(row["mean_confidence"] or 0.0) * min(
                1.0, math.log1p(mentions) / math.log(11)
            )
            await self._attributes.upsert_hotel_score(
                {
                    "hotel_id": hotel_id,
                    "attribute_id": row["attribute_id"],
                    "algorithm_version_id": algorithm.id,
                    "mention_count": mentions,
                    "positive_mentions": int(row["positive_mentions"]),
                    "negative_mentions": int(row["negative_mentions"]),
                    "neutral_mentions": int(row["neutral_mentions"]),
                    "raw_score": raw,
                    "score_5": raw,
                    "score_100": score_100(raw),
                    "mean": None,
                    "standard_deviation": None,
                    "z_score": None,
                    "relative_score_5": None,
                    "confidence_score": confidence,
                    "calculated_at": now,
                }
            )
        await self._session.commit()
        get_logger().info("scoring_completed", hotel_id=str(hotel_id), attributes=len(rows))
        return len(rows)

    async def has_pending_reviews(self) -> bool:
        """Return whether retry-bounded extraction work remains."""
        algorithm = await self._attributes.active_algorithm()
        if algorithm is None:
            raise ScoringError("No active algorithm version")
        pending = await self._reviews.has_unprocessed(algorithm.id)
        await self._session.commit()
        return pending

    async def recalculate_all(self) -> int:
        """Recalculate every hotel with persisted mentions."""
        hotel_ids = await self._attributes.hotel_ids_for_scoring()
        total = 0
        for hotel_id in hotel_ids:
            total += await self.recalculate_hotel(hotel_id)
        return total

    async def normalize(
        self,
        scope_type: ScopeType = ScopeType.GLOBAL,
        country_id: uuid.UUID | None = None,
        region_id: uuid.UUID | None = None,
        city_id: uuid.UUID | None = None,
        hotel_type: str | None = None,
        hotel_ids: list[uuid.UUID] | None = None,
    ) -> int:
        """Bell-curve normalize scores across an explicit comparable scope."""
        self._validate_scope(
            scope_type, country_id, region_id, city_id, hotel_ids
        )
        algorithm = await self._attributes.active_algorithm()
        if algorithm is None:
            raise ScoringError("No active algorithm version")
        attribute_ids = await self._attributes.attribute_ids_with_scores(algorithm.id)
        updated = 0
        for attribute_id in attribute_ids:
            population = await self._attributes.score_population(
                attribute_id,
                algorithm.id,
                country_id,
                region_id,
                city_id,
                hotel_type,
                hotel_ids,
            )
            if not population:
                continue
            stats = population_statistics(
                [
                    float(item.score_5)
                    for item in population
                    if item.score_5 is not None
                ]
            )
            self._session.add(
                NormalizationRun(
                    attribute_id=attribute_id,
                    scope_type=scope_type.value,
                    country_id=country_id,
                    region_id=region_id,
                    city_id=city_id,
                    hotel_type=hotel_type,
                    population_size=stats.size,
                    mean_score=stats.mean,
                    standard_deviation=stats.standard_deviation,
                    algorithm_version_id=algorithm.id,
                    calculated_at=datetime.now(UTC),
                )
            )
            for item in population:
                if item.score_5 is None:
                    continue
                z, relative = normalize_value(float(item.score_5), stats)
                item.mean = Decimal(str(stats.mean))
                item.standard_deviation = Decimal(str(stats.standard_deviation))
                item.z_score = Decimal(str(z))
                item.relative_score_5 = Decimal(str(relative))
                updated += 1
        await self._session.commit()
        get_logger().info("normalization_completed", scope=scope_type.value, updated=updated)
        return updated

    @staticmethod
    def _mention_model(
        review: Review,
        algorithm_version_id: uuid.UUID,
        attribute_id: uuid.UUID,
        output: object,
    ) -> ReviewAttributeMention:
        from app.integrations.llm.base import AttributeMentionOutput

        mention = AttributeMentionOutput.model_validate(output)
        sentiment_value = {
            Sentiment.POSITIVE: 1.0,
            Sentiment.NEGATIVE: -1.0,
            Sentiment.NEUTRAL: 0.0,
        }[mention.sentiment]
        rating = float(review.normalized_rating_5)
        return ReviewAttributeMention(
            review_id=review.id,
            attribute_id=attribute_id,
            sentiment=mention.sentiment.value,
            sentiment_value=sentiment_value,
            review_rating_used=rating,
            calculated_contribution=calculate_contribution(rating, mention.sentiment),
            confidence=mention.confidence,
            evidence_text=mention.evidence_text,
            algorithm_version_id=algorithm_version_id,
            created_at=datetime.now(UTC),
        )

    @staticmethod
    def _validate_scope(
        scope_type: ScopeType,
        country_id: uuid.UUID | None,
        region_id: uuid.UUID | None,
        city_id: uuid.UUID | None,
        hotel_ids: list[uuid.UUID] | None,
    ) -> None:
        required = {
            ScopeType.GLOBAL: True,
            ScopeType.COUNTRY: country_id is not None,
            ScopeType.REGION: region_id is not None,
            ScopeType.CITY: city_id is not None,
            ScopeType.SEARCH_SET: bool(hotel_ids),
        }
        if not required[scope_type]:
            raise ScoringError("Normalization scope is missing its required identifier")

"""Candidate query and recommendation result persistence."""

import uuid
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AlgorithmVersion,
    Attribute,
    Hotel,
    HotelAttributeScore,
    HotelRecommendation,
    HotelSourceMapping,
    RecommendationRun,
    Trip,
)


class RecommendationRepository:
    """Load precomputed scoring inputs and persist live rankings."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def candidates(
        self,
        trip: Trip,
        algorithm_version: AlgorithmVersion,
        hotel_type: str | None,
        min_source_rating: float | None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """Load destination hotels and all available precomputed scores."""
        candidate_ids = select(Hotel.id).where(
            Hotel.country_id == trip.destination_country_id,
            Hotel.is_active,
            Hotel.deleted_at.is_(None),
        )
        if trip.destination_region_id:
            candidate_ids = candidate_ids.where(Hotel.region_id == trip.destination_region_id)
        if trip.destination_city_id:
            candidate_ids = candidate_ids.where(Hotel.city_id == trip.destination_city_id)
        if hotel_type:
            candidate_ids = candidate_ids.where(Hotel.hotel_type == hotel_type)
        if min_source_rating is not None:
            candidate_ids = candidate_ids.where(
                Hotel.id.in_(
                    select(HotelSourceMapping.hotel_id).where(
                        HotelSourceMapping.source_rating >= min_source_rating,
                        HotelSourceMapping.is_active,
                    )
                )
            )
        selected_ids = candidate_ids.order_by(Hotel.id).limit(limit).subquery()
        query = (
            select(
                Hotel.id.label("hotel_id"),
                Hotel.name,
                Hotel.hotel_type,
                HotelAttributeScore.attribute_id,
                Attribute.name.label("attribute_name"),
                Attribute.slug.label("attribute_slug"),
                HotelAttributeScore.score_5,
                HotelAttributeScore.relative_score_5,
                HotelAttributeScore.confidence_score,
            )
            .outerjoin(
                HotelAttributeScore,
                and_(
                    HotelAttributeScore.hotel_id == Hotel.id,
                    HotelAttributeScore.algorithm_version_id == algorithm_version.id,
                ),
            )
            .outerjoin(Attribute, Attribute.id == HotelAttributeScore.attribute_id)
            .where(
                Hotel.id.in_(select(selected_ids.c.id)),
            )
            .order_by(Hotel.id)
        )
        return [dict(row._mapping) for row in (await self._session.execute(query)).all()]

    async def save(
        self,
        run: RecommendationRun,
        results: list[HotelRecommendation],
    ) -> RecommendationRun:
        """Atomically stage a run and all result rows."""
        self._session.add(run)
        await self._session.flush()
        self._session.add_all(results)
        await self._session.flush()
        return run

    async def get_owned(
        self, run_id: uuid.UUID, user_id: uuid.UUID
    ) -> RecommendationRun | None:
        """Return a recommendation run only to its owner."""
        return await self._session.scalar(
            select(RecommendationRun).where(
                RecommendationRun.id == run_id, RecommendationRun.user_id == user_id
            )
        )

    async def result_rows(self, run_id: uuid.UUID) -> list[dict[str, Any]]:
        """Return ordered persisted results joined to hotel names."""
        query = (
            select(
                HotelRecommendation.rank,
                HotelRecommendation.hotel_id,
                Hotel.name,
                HotelRecommendation.personalized_rating,
                HotelRecommendation.match_score,
                HotelRecommendation.coverage_score,
                HotelRecommendation.explanation,
            )
            .join(Hotel, Hotel.id == HotelRecommendation.hotel_id)
            .where(HotelRecommendation.recommendation_run_id == run_id)
            .order_by(HotelRecommendation.rank)
        )
        return [dict(row._mapping) for row in (await self._session.execute(query)).all()]

    async def result(self, run_id: uuid.UUID, hotel_id: uuid.UUID) -> HotelRecommendation | None:
        """Return one persisted recommended hotel."""
        return await self._session.scalar(
            select(HotelRecommendation).where(
                HotelRecommendation.recommendation_run_id == run_id,
                HotelRecommendation.hotel_id == hotel_id,
            )
        )

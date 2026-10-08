"""Live deterministic personalized hotel ranking and explanations."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.cache import Cache
from app.common.logging import get_logger
from app.common.observability import RECOMMENDATIONS
from app.domain.ranking import EffectivePreference, RankedHotel, rank_hotels
from app.dto.recommendations import (
    RecommendationDetail,
    RecommendationRequest,
    RecommendationResult,
    RecommendationRunRead,
)
from app.exceptions import (
    OnboardingNotCompletedError,
    RecommendationRunNotFoundError,
    ScoringError,
    TripNotFoundError,
)
from app.models import HotelRecommendation, RecommendationRun
from app.repositories.catalog import AttributeRepository
from app.repositories.preferences import PreferenceRepository
from app.repositories.recommendations import RecommendationRepository
from app.repositories.trips import TripRepository
from app.repositories.users import UserRepository


class RecommendationService:
    """Orchestrate live ranking from precomputed attribute scores."""

    def __init__(self, session: AsyncSession, cache: Cache | None = None) -> None:
        self._session = session
        self._cache = cache
        self._users = UserRepository(session)
        self._trips = TripRepository(session)
        self._preferences = PreferenceRepository(session)
        self._attributes = AttributeRepository(session)
        self._recommendations = RecommendationRepository(session)

    async def generate(
        self, user_id: uuid.UUID, payload: RecommendationRequest
    ) -> RecommendationRunRead:
        """Calculate, persist, and return a live deterministic ranking."""
        user = await self._users.get_by_id(user_id)
        if user is None or (
            not user.onboarding_completed and user.onboarding_skipped_at is None
        ):
            raise OnboardingNotCompletedError()
        trip = await self._trips.get_owned(payload.trip_id, user_id)
        if trip is None:
            raise TripNotFoundError()
        algorithm = await self._attributes.active_algorithm()
        if algorithm is None:
            raise ScoringError("No active algorithm version")
        raw_preferences = await self._preferences.resolve_for_trip(user_id, trip.id)
        preferences = [
            EffectivePreference(
                attribute_id=item["attribute_id"],
                weight=float(item["importance_weight"]),
                minimum_required_score=(
                    float(item["minimum_required_score"])
                    if item.get("minimum_required_score") is not None
                    else None
                ),
                is_mandatory=bool(item.get("is_mandatory", False)),
            )
            for item in raw_preferences
            if float(item["importance_weight"]) > 0
        ]
        candidates = await self._recommendations.candidates(
            trip, algorithm, payload.hotel_type, payload.min_source_rating
        )
        ranked = (
            rank_hotels(candidates, preferences)
            if preferences
            else self._rank_without_preferences(candidates)
        )[: payload.limit]
        run_id = uuid.uuid4()
        run = RecommendationRun(
            id=run_id,
            user_id=user_id,
            trip_id=trip.id,
            algorithm_version_id=algorithm.id,
            preference_snapshot=[
                {
                    "attribute_id": str(item.attribute_id),
                    "weight": item.weight,
                    "minimum_required_score": item.minimum_required_score,
                    "is_mandatory": item.is_mandatory,
                }
                for item in preferences
            ],
            filters=payload.model_dump(mode="json", exclude={"trip_id"}),
            candidate_count=len({row["hotel_id"] for row in candidates}),
            result_count=len(ranked),
            created_at=datetime.now(UTC),
        )
        models = [
            HotelRecommendation(
                recommendation_run_id=run_id,
                hotel_id=item.hotel_id,
                rank=index,
                personalized_rating=item.personalized_rating,
                match_score=item.match_score,
                coverage_score=item.coverage_score,
                explanation=item.explanation,
            )
            for index, item in enumerate(ranked, start=1)
        ]
        await self._recommendations.save(run, models)
        await self._session.commit()
        RECOMMENDATIONS.labels("success").inc()
        get_logger().info(
            "recommendation_generated",
            user_id=str(user_id),
            trip_id=str(trip.id),
            run_id=str(run_id),
            results=len(ranked),
        )
        result = self._to_run(run_id, trip.id, ranked)
        await self._store_run(user_id, result)
        return result

    async def get(self, user_id: uuid.UUID, run_id: uuid.UUID) -> RecommendationRunRead:
        """Return a persisted recommendation run to its owner."""
        cache_key = self._run_cache_key(user_id, run_id)
        if self._cache:
            try:
                cached = await self._cache.get_json(cache_key)
                if cached is not None:
                    return RecommendationRunRead.model_validate(cached)
            except Exception:
                get_logger().warning("cache_read_failed", key=cache_key)
        run = await self._recommendations.get_owned(run_id, user_id)
        if run is None:
            raise RecommendationRunNotFoundError()
        rows = await self._recommendations.result_rows(run_id)
        results = [
            RecommendationResult(
                **row,
                source_rating=row["explanation"].get("source_rating"),
                source_review_count=row["explanation"].get("source_review_count"),
                source_rank=row["explanation"].get("source_rank"),
                matched_attributes=_matched_attributes(row["explanation"]),
                reasons=list(row["explanation"].get("reasons", [])),
            )
            for row in rows
        ]
        result = RecommendationRunRead(
            recommendation_run_id=run.id, trip_id=run.trip_id, results=results
        )
        await self._store_run(user_id, result)
        return result

    async def detail(
        self, user_id: uuid.UUID, run_id: uuid.UUID, hotel_id: uuid.UUID
    ) -> RecommendationDetail:
        """Return one recommendation explanation after ownership validation."""
        if await self._recommendations.get_owned(run_id, user_id) is None:
            raise RecommendationRunNotFoundError()
        result = await self._recommendations.result(run_id, hotel_id)
        if result is None:
            raise RecommendationRunNotFoundError("Hotel is not part of this recommendation run")
        return RecommendationDetail(
            recommendation_run_id=run_id,
            hotel_id=hotel_id,
            personalized_rating=float(result.personalized_rating),
            match_score=float(result.match_score),
            coverage_score=float(result.coverage_score),
            source_rating=result.explanation.get("source_rating"),
            source_review_count=result.explanation.get("source_review_count"),
            source_rank=result.explanation.get("source_rank"),
            matched_attributes=_matched_attributes(result.explanation),
            explanation=result.explanation,
        )

    async def _store_run(
        self, user_id: uuid.UUID, result: RecommendationRunRead
    ) -> None:
        if self._cache is None:
            return
        key = self._run_cache_key(user_id, result.recommendation_run_id)
        try:
            await self._cache.set_json(key, result.model_dump(mode="json"), 900)
        except Exception:
            get_logger().warning("cache_write_failed", key=key)

    @staticmethod
    def _run_cache_key(user_id: uuid.UUID, run_id: uuid.UUID) -> str:
        return f"recommendation:user:{user_id}:run:{run_id}:v1"

    @staticmethod
    def _to_run(
        run_id: uuid.UUID, trip_id: uuid.UUID, ranked: list[RankedHotel]
    ) -> RecommendationRunRead:
        return RecommendationRunRead(
            recommendation_run_id=run_id,
            trip_id=trip_id,
            results=[
                RecommendationResult(
                    rank=index,
                    hotel_id=item.hotel_id,
                    name=item.name,
                    personalized_rating=item.personalized_rating,
                    match_score=item.match_score,
                    coverage_score=item.coverage_score,
                    source_rating=item.source_rating,
                    source_review_count=item.source_review_count,
                    source_rank=item.source_rank,
                    matched_attributes=_matched_attributes(item.explanation),
                    reasons=list(item.explanation["reasons"]),
                    explanation=item.explanation,
                )
                for index, item in enumerate(ranked, start=1)
            ],
        )

    @staticmethod
    def _rank_without_preferences(
        candidate_rows: list[dict[str, object]],
    ) -> list[RankedHotel]:
        """Return deterministic catalog candidates without fabricating personalization."""
        ranked: list[RankedHotel] = []
        seen: set[uuid.UUID] = set()
        for row in candidate_rows:
            hotel_id = row["hotel_id"]
            if not isinstance(hotel_id, uuid.UUID) or hotel_id in seen:
                continue
            seen.add(hotel_id)
            source_rating = _as_float(row.get("source_rating"))
            source_review_count = _as_int(row.get("source_review_count"))
            source_rank = _as_int(row.get("source_rank"))
            ranked.append(
                RankedHotel(
                    hotel_id=hotel_id,
                    name=str(row["name"]),
                    personalized_rating=0.0,
                    match_score=0.0,
                    coverage_score=0.0,
                    source_rating=source_rating,
                    source_review_count=source_review_count,
                    source_rank=source_rank,
                    explanation={
                        "matched_attributes": [],
                        "strengths": [],
                        "weaknesses": [],
                        "reasons": [
                            "No onboarding preferences available; showing catalog matches"
                        ],
                        "coverage_score": 0.0,
                        "coverage_weight": 0.0,
                        "requested_weight": 0.0,
                        "source_rating": source_rating,
                        "source_review_count": source_review_count,
                        "source_rank": source_rank,
                    },
                )
            )
        return ranked


def _matched_attributes(explanation: dict[str, object]) -> list[dict[str, object]]:
    """Return persisted score evidence in the public result shape."""
    values = explanation.get("matched_attributes", [])
    return list(values) if isinstance(values, list) else []


def _as_float(value: object) -> float | None:
    return float(value) if value is not None else None


def _as_int(value: object) -> int | None:
    return int(value) if value is not None else None

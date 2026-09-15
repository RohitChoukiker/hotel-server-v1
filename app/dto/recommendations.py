"""Live deterministic recommendation DTOs."""

import uuid
from typing import Any

from pydantic import Field

from app.dto.common import DTO


class RecommendationRequest(DTO):
    """Live ranking request for a user-owned trip."""

    trip_id: uuid.UUID
    limit: int = Field(default=20, ge=1, le=100)
    hotel_type: str | None = None
    min_source_rating: float | None = Field(default=None, ge=0, le=5)


class MatchedAttribute(DTO):
    """One explainability factor."""

    attribute: str
    score: float
    weight: float


class RecommendationResult(DTO):
    """Ranked personalized hotel result."""

    rank: int
    hotel_id: uuid.UUID
    name: str
    personalized_rating: float
    match_score: float
    coverage_score: float
    reasons: list[str]
    explanation: dict[str, Any]


class RecommendationRunRead(DTO):
    """Persisted recommendation run with results."""

    recommendation_run_id: uuid.UUID
    trip_id: uuid.UUID
    results: list[RecommendationResult]


class RecommendationDetail(DTO):
    """Full explanation for one recommended hotel."""

    recommendation_run_id: uuid.UUID
    hotel_id: uuid.UUID
    personalized_rating: float
    match_score: float
    explanation: dict[str, Any]


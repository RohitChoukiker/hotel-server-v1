"""Pure live personalized ranking from precomputed hotel scores."""

import uuid
from dataclasses import dataclass
from typing import Any

from app.exceptions import ScoringError


@dataclass(frozen=True, slots=True)
class EffectivePreference:
    """Ranking-ready effective attribute preference."""

    attribute_id: uuid.UUID
    weight: float
    minimum_required_score: float | None
    is_mandatory: bool


@dataclass(frozen=True, slots=True)
class RankedHotel:
    """Pure deterministic ranking result before persistence."""

    hotel_id: uuid.UUID
    name: str
    personalized_rating: float
    match_score: float
    coverage_score: float
    source_rating: float | None
    source_review_count: int | None
    source_rank: int | None
    explanation: dict[str, Any]


def rank_hotels(
    candidate_rows: list[dict[str, Any]],
    preferences: list[EffectivePreference],
) -> list[RankedHotel]:
    """Rank hotels by weighted precomputed scores; never inspect raw reviews."""
    by_hotel: dict[uuid.UUID, dict[str, Any]] = {}
    for row in candidate_rows:
        hotel_id = row["hotel_id"]
        hotel = by_hotel.setdefault(
            hotel_id,
            {
                "name": row["name"],
                "source_rating": _number(row.get("source_rating")),
                "source_review_count": _integer(row.get("source_review_count")),
                "source_rank": _integer(row.get("source_rank")),
                "scores": {},
            },
        )
        attribute_id = row.get("attribute_id")
        if attribute_id:
            value = row.get("relative_score_5")
            if value is not None:
                hotel["scores"][attribute_id] = {
                    "value": float(value),
                    "name": row.get("attribute_name") or str(attribute_id),
                    "attribute_id": attribute_id,
                    "score_5": row.get("score_5"),
                    "score_100": row.get("score_100"),
                    "relative_score_5": row.get("relative_score_5"),
                    "confidence_score": row.get("confidence_score"),
                    "mention_count": row.get("mention_count"),
                    "positive_mentions": row.get("positive_mentions"),
                    "negative_mentions": row.get("negative_mentions"),
                    "neutral_mentions": row.get("neutral_mentions"),
                    "scoring_source": row.get("scoring_source"),
                }
    total_weight = sum(item.weight for item in preferences)
    if total_weight <= 0:
        raise ScoringError("At least one positive preference weight is required")
    ranked: list[RankedHotel] = []
    for hotel_id, hotel in by_hotel.items():
        result = _rank_one(hotel_id, hotel, preferences, total_weight)
        if result is not None:
            ranked.append(result)
    # No existing personalized tie rule exists. Use catalog source metadata as
    # secondary ordering, then canonical id ascending. Source rank is never
    # used as personalized rank.
    return sorted(
        ranked,
        key=lambda item: (
            -item.personalized_rating,
            -item.coverage_score,
            -(item.source_rating if item.source_rating is not None else -1.0),
            -(item.source_review_count if item.source_review_count is not None else -1),
            str(item.hotel_id),
        ),
    )


def _rank_one(
    hotel_id: uuid.UUID,
    hotel: dict[str, Any],
    preferences: list[EffectivePreference],
    total_weight: float,
) -> RankedHotel | None:
    weighted_sum = 0.0
    available_weight = 0.0
    matched: list[dict[str, Any]] = []
    for preference in preferences:
        score = hotel["scores"].get(preference.attribute_id)
        if score is None:
            if preference.is_mandatory:
                return None
            continue
        value = float(score["value"])
        if (
            preference.is_mandatory
            and preference.minimum_required_score is not None
            and value < preference.minimum_required_score
        ):
            return None
        weighted_sum += value * preference.weight
        available_weight += preference.weight
        matched.append(
            {
                "attribute_id": str(score["attribute_id"]),
                "attribute": score["name"],
                "score": round(value, 3),
                "weight": round(preference.weight, 4),
                "score_5": _number(score.get("score_5")),
                "score_100": _number(score.get("score_100")),
                "relative_score_5": _number(score.get("relative_score_5")),
                "confidence_score": _number(score.get("confidence_score")),
                "mention_count": score.get("mention_count"),
                "positive_mentions": score.get("positive_mentions"),
                "negative_mentions": score.get("negative_mentions"),
                "neutral_mentions": score.get("neutral_mentions"),
                "scoring_source": score.get("scoring_source"),
            }
        )
    if available_weight == 0:
        return None
    rating = weighted_sum / available_weight
    personalized_rating = round(rating, 3)
    coverage = available_weight / total_weight
    strengths = [item["attribute"] for item in matched if item["score"] >= 4.0]
    weaknesses = [item["attribute"] for item in matched if item["score"] < 3.0]
    reasons = [f"Strong match for {name}" for name in strengths[:3]] or [
        "Best available match for your weighted preferences"
    ]
    explanation = {
        "matched_attributes": sorted(
            matched,
            key=lambda item: item["score"] * item["weight"],
            reverse=True,
        ),
        "strengths": strengths,
        "weaknesses": weaknesses,
        "reasons": reasons,
        "coverage_score": round(coverage, 4),
        "coverage_weight": round(available_weight, 4),
        "requested_weight": round(total_weight, 4),
        "source_rating": hotel.get("source_rating"),
        "source_review_count": hotel.get("source_review_count"),
        "source_rank": hotel.get("source_rank"),
    }
    return RankedHotel(
        hotel_id=hotel_id,
        name=str(hotel["name"]),
        personalized_rating=personalized_rating,
        match_score=round(personalized_rating / 5.0 * 100.0, 2),
        coverage_score=round(coverage, 4),
        source_rating=(
            float(hotel["source_rating"])
            if hotel.get("source_rating") is not None
            else None
        ),
        source_review_count=(
            int(hotel["source_review_count"])
            if hotel.get("source_review_count") is not None
            else None
        ),
        source_rank=(int(hotel["source_rank"]) if hotel.get("source_rank") is not None else None),
        explanation=explanation,
    )


def _number(value: Any) -> float | None:
    """Convert Decimal-like database values for JSON-safe explanations."""
    return float(value) if value is not None else None


def _integer(value: Any) -> int | None:
    """Convert nullable database integer-like values."""
    return int(value) if value is not None else None

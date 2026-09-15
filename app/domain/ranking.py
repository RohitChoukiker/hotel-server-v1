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
    explanation: dict[str, Any]


def rank_hotels(
    candidate_rows: list[dict[str, Any]],
    preferences: list[EffectivePreference],
) -> list[RankedHotel]:
    """Rank hotels by weighted precomputed scores; never inspect raw reviews."""
    by_hotel: dict[uuid.UUID, dict[str, Any]] = {}
    for row in candidate_rows:
        hotel_id = row["hotel_id"]
        hotel = by_hotel.setdefault(hotel_id, {"name": row["name"], "scores": {}})
        attribute_id = row.get("attribute_id")
        if attribute_id:
            value = row.get("relative_score_5")
            if value is None:
                value = row.get("score_5")
            if value is not None:
                hotel["scores"][attribute_id] = {
                    "value": float(value),
                    "name": row.get("attribute_name") or str(attribute_id),
                }
    total_weight = sum(item.weight for item in preferences)
    if total_weight <= 0:
        raise ScoringError("At least one positive preference weight is required")
    ranked: list[RankedHotel] = []
    for hotel_id, hotel in by_hotel.items():
        result = _rank_one(hotel_id, hotel, preferences, total_weight)
        if result is not None:
            ranked.append(result)
    return sorted(
        ranked,
        key=lambda item: (item.personalized_rating, item.coverage_score, str(item.hotel_id)),
        reverse=True,
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
                "attribute": score["name"],
                "score": round(value, 3),
                "weight": round(preference.weight, 4),
            }
        )
    if available_weight == 0:
        return None
    rating = weighted_sum / available_weight
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
    }
    return RankedHotel(
        hotel_id=hotel_id,
        name=str(hotel["name"]),
        personalized_rating=round(rating, 3),
        match_score=round(rating / 5.0 * 100.0, 2),
        coverage_score=round(coverage, 4),
        explanation=explanation,
    )


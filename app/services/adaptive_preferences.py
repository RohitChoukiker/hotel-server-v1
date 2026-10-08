"""Deterministic answer-to-preference mapping for adaptive onboarding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.dto.onboarding import CANONICAL_ADAPTIVE_QUESTION_KEYS

_NORMALIZATION_SCALE = 3.0
_SUPPORTED_ATTRIBUTE_SLUGS = frozenset(
    {
        "bathroom",
        "beach-access",
        "bed-comfort",
        "breakfast",
        "city-center-access",
        "cleanliness",
        "gym",
        "nearby-restaurants",
        "nightlife",
        "parking",
        "pool",
        "public-transport",
        "quietness",
        "restaurant",
        "room-comfort",
        "room-condition",
        "room-service",
        "room-size",
        "room-view",
        "spa",
        "tourist-attractions",
        "wifi",
    }
)

_DIMENSION_EFFECTS: dict[str, tuple[tuple[str, float], ...]] = {
    "location": (
        ("city-center-access", 0.45),
        ("public-transport", 0.35),
        ("tourist-attractions", 0.45),
    ),
    "room_quality": (
        ("room-comfort", 0.55),
        ("room-condition", 0.45),
        ("cleanliness", 0.35),
    ),
    "food": (("restaurant", 0.5), ("breakfast", 0.35), ("nearby-restaurants", 0.45)),
    "amenities": (("spa", 0.4), ("pool", 0.35), ("wifi", 0.25)),
    "hospitality": (("room-service", 0.5), ("restaurant", 0.3)),
    "activities": (("tourist-attractions", 0.55), ("public-transport", 0.25)),
    "privacy": (("quietness", 0.65),),
    "social": (("nightlife", 0.6), ("nearby-restaurants", 0.35)),
}

_VALUE_EFFECTS: dict[str, dict[str, tuple[tuple[str, float], ...]]] = {
    "price_value": {
        "budget_conscious": (),
        "best_value": (("room-comfort", 0.35),),
        "balanced_value": (("room-comfort", 0.55), ("room-condition", 0.35)),
        "premium_quality": (
            ("room-comfort", 0.8),
            ("room-condition", 0.7),
            ("spa", 0.4),
        ),
        "luxury_experience": (
            ("room-comfort", 0.85),
            ("room-condition", 0.75),
            ("spa", 0.7),
            ("room-service", 0.55),
        ),
    },
    "travel_pace": {
        "active_packed": (
            ("tourist-attractions", 0.9),
            ("city-center-access", 0.45),
            ("public-transport", 0.35),
        ),
        "mixed_pace": (
            ("tourist-attractions", 0.5),
            ("city-center-access", 0.3),
            ("room-comfort", 0.3),
        ),
        "mixed": (
            ("tourist-attractions", 0.5),
            ("city-center-access", 0.3),
            ("room-comfort", 0.3),
        ),
        "restful_relaxed": (
            ("quietness", 0.9),
            ("room-comfort", 0.65),
            ("spa", 0.5),
            ("room-view", 0.35),
        ),
        "restful": (
            ("quietness", 0.9),
            ("room-comfort", 0.65),
            ("spa", 0.5),
            ("room-view", 0.35),
        ),
        "relaxed": (
            ("quietness", 0.9),
            ("room-comfort", 0.65),
            ("spa", 0.5),
            ("room-view", 0.35),
        ),
    },
    "food_preference": {
        "eating_out": (("nearby-restaurants", 0.9), ("restaurant", 0.45)),
        "hotel_dining": (("restaurant", 0.85), ("room-service", 0.75), ("breakfast", 0.45)),
        "mix": (("nearby-restaurants", 0.6), ("restaurant", 0.6), ("breakfast", 0.45)),
    },
    "travel_group": {},
    "couple_style": {
        "romance": (
            ("quietness", 0.8),
            ("spa", 0.65),
            ("room-service", 0.55),
            ("room-comfort", 0.55),
        ),
        "adventure": (
            ("tourist-attractions", 0.9),
            ("beach-access", 0.55),
            ("public-transport", 0.35),
        ),
        "decompression": (
            ("quietness", 1.0),
            ("room-comfort", 0.8),
            ("spa", 0.65),
            ("room-service", 0.45),
        ),
        "relaxation": (
            ("quietness", 1.0),
            ("room-comfort", 0.8),
            ("spa", 0.65),
            ("room-service", 0.45),
        ),
    },
    "family_needs": {
        "kids": (("room-size", 0.8), ("pool", 0.6), ("parking", 0.35), ("bathroom", 0.3)),
        "family_friendly": (
            ("room-size", 0.8),
            ("pool", 0.6),
            ("parking", 0.35),
            ("bathroom", 0.3),
        ),
        "convenience": (("room-size", 0.55), ("parking", 0.45), ("room-service", 0.35)),
    },
    "friends_vibe": {
        "social": (
            ("nightlife", 0.85),
            ("tourist-attractions", 0.65),
            ("city-center-access", 0.45),
        ),
        "adventure": (
            ("tourist-attractions", 0.85),
            ("public-transport", 0.4),
            ("beach-access", 0.35),
        ),
        "relaxed": (("quietness", 0.45), ("room-comfort", 0.45), ("room-size", 0.35)),
    },
    "beach_activity": {
        "water_sports": (("beach-access", 1.0), ("tourist-attractions", 0.5)),
        "swimming": (("beach-access", 0.9),),
        "relaxation": (("beach-access", 0.8), ("quietness", 0.45)),
    },
    "beach_proximity": {"near_beach": (("beach-access", 1.0),)},
    "mountain_outdoors": {
        "outdoors": (("tourist-attractions", 0.9), ("public-transport", 0.35)),
        "adventure": (("tourist-attractions", 0.9), ("public-transport", 0.35)),
    },
    "mountain_scenery": {
        "scenery": (("room-view", 0.9), ("quietness", 0.45)),
        "views": (("room-view", 0.9), ("quietness", 0.45)),
    },
    "mountain_recovery": {
        "recovery": (("quietness", 0.9), ("spa", 0.6), ("room-comfort", 0.5)),
        "rest": (("quietness", 0.9), ("spa", 0.6), ("room-comfort", 0.5)),
    },
    "city_focus": {
        "culture": (("tourist-attractions", 0.8), ("city-center-access", 0.55)),
        "food": (("nearby-restaurants", 0.75), ("restaurant", 0.55)),
        "nightlife": (("nightlife", 0.85), ("city-center-access", 0.55)),
        "peaceful_retreat": (("quietness", 0.9), ("room-view", 0.45)),
    },
    "city_location": {
        "central": (("city-center-access", 0.9), ("public-transport", 0.65)),
        "convenient": (("city-center-access", 0.8), ("public-transport", 0.6)),
        "peaceful": (("quietness", 0.8), ("room-view", 0.35)),
    },
    "rural_isolation": {
        "peaceful_isolation": (("quietness", 1.0), ("room-view", 0.6), ("room-comfort", 0.4)),
        "isolation": (("quietness", 1.0), ("room-view", 0.6), ("room-comfort", 0.4)),
        "peaceful_retreat": (("quietness", 1.0), ("room-view", 0.6), ("room-comfort", 0.4)),
    },
    "rural_stay_style": {
        "nature": (("quietness", 0.7), ("room-view", 0.55)),
        "cozy": (("quietness", 0.65), ("room-comfort", 0.6)),
        "resort": (("quietness", 0.55), ("spa", 0.55), ("room-comfort", 0.5)),
    },
    "destination_detail": {
        "peaceful_isolation": (("quietness", 1.0), ("room-view", 0.6), ("room-comfort", 0.45)),
        "peaceful_retreat": (("quietness", 1.0), ("room-view", 0.6), ("room-comfort", 0.45)),
        "scenery": (("room-view", 0.9), ("quietness", 0.4)),
        "outdoors": (("tourist-attractions", 0.9), ("public-transport", 0.35)),
        "culture": (("tourist-attractions", 0.75), ("city-center-access", 0.4)),
        "food": (("nearby-restaurants", 0.75), ("restaurant", 0.5)),
        "nightlife": (("nightlife", 0.8), ("city-center-access", 0.45)),
    },
}

_GROUP_EFFECTS: dict[str, tuple[tuple[str, float], ...]] = {
    "family": _VALUE_EFFECTS["family_needs"].get("family_friendly", ()),
    "family_with_kids": _VALUE_EFFECTS["family_needs"].get("family_friendly", ()),
    "friends": _VALUE_EFFECTS["friends_vibe"].get("social", ()),
    "couple": _VALUE_EFFECTS["couple_style"].get("decompression", ()),
}

_CANONICAL_DIMENSIONS = frozenset(
    {
        "activities",
        "food",
        "friends_vibe",
        "family_needs",
        "hospitality",
        "kids",
        "location",
        "price",
        "privacy",
        "room_quality",
        "social",
    }
)

_KEY_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "price_value": ("price",),
    "travel_pace": ("activities",),
    "food_preference": ("food",),
    "couple_style": ("privacy", "hospitality"),
    "family_needs": ("kids", "room_quality"),
    "friends_vibe": ("social", "activities"),
    "beach_activity": ("location", "activities"),
    "beach_proximity": ("location",),
    "mountain_outdoors": ("location", "activities"),
    "mountain_scenery": ("location",),
    "mountain_recovery": ("location", "privacy"),
    "city_focus": ("location",),
    "city_location": ("location",),
    "rural_isolation": ("location", "privacy"),
    "rural_stay_style": ("location", "privacy"),
    "destination_detail": ("location",),
}


@dataclass(frozen=True)
class DeterministicAdaptiveProfile:
    """Stable profile projection produced from persisted onboarding answers."""

    profile_summary: str
    confidence: float
    context: dict[str, Any]
    preferences: list[dict[str, Any]]
    unresolved_attributes: list[str]
    signals: dict[str, Any]


def build_deterministic_adaptive_profile(
    rows: list[tuple[Any, Any]],
    location_context: dict[str, Any],
    routing_state: dict[str, Any] | None = None,
) -> DeterministicAdaptiveProfile:
    """Map explicit persisted answers to supported taxonomy attributes."""
    scores: dict[str, float] = {}
    evidence: dict[str, set[str]] = {}
    canonical_scores: dict[str, float] = {}
    answer_values: dict[str, list[str]] = {}
    seen_questions: set[str] = set()

    ordered_rows = sorted(
        rows,
        key=lambda item: (
            int(getattr(item[0], "position", 0) or 0),
            str(getattr(item[0], "id", "")),
        ),
    )
    for question, answer_row in ordered_rows:
        answer = getattr(answer_row, "answer", None)
        if not isinstance(answer, dict):
            continue
        key = _canonical_question_key(question, answer)
        if key is None:
            continue
        question_identity = str(
            getattr(question, "id", None)
            or f"{getattr(question, 'position', '')}:{key}:{getattr(question, 'prompt', '')}"
        )
        if question_identity in seen_questions:
            continue
        seen_questions.add(question_identity)
        values = _answer_values(answer)
        if not values:
            continue
        answer_values.setdefault(key, [])
        answer_values[key].extend(value for value in values if value not in answer_values[key])
        for value in values:
            effects = _effects_for(key, value)
            dimensions = _dimensions_for(question)
            if not effects:
                effects = _dimension_effects(dimensions)
            _add_effects(scores, evidence, effects, question_identity)
            for dimension in (*_KEY_DIMENSIONS.get(key, ()), *sorted(dimensions)):
                canonical_scores[dimension] = canonical_scores.get(dimension, 0.0) + 1.0

    group_values = answer_values.get("travel_group", [])
    for group in group_values:
        _add_effects(scores, evidence, _GROUP_EFFECTS.get(group, ()), "group")
        if group.startswith("family"):
            canonical_scores["kids"] = canonical_scores.get("kids", 0.0) + 1.0

    preferences = [
        {
            "attribute_slug": slug,
            "importance_weight": round(min(1.0, raw_score / _NORMALIZATION_SCALE), 4),
            "preferred_value": None,
            "is_mandatory": False,
            "confidence": 1.0,
            "evidence_question_ids": sorted(evidence.get(slug, set())),
        }
        for slug, raw_score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        if slug in _SUPPORTED_ATTRIBUTE_SLUGS and raw_score > 0
    ]
    preferences.sort(key=lambda item: (-item["importance_weight"], item["attribute_slug"]))
    unresolved = sorted(
        dimension for dimension in canonical_scores if dimension in {"price", "kids"}
    )
    context = _profile_context(location_context)
    context["group_type"] = (answer_values.get("travel_group") or [None])[0]
    context["travel_pace"] = (answer_values.get("travel_pace") or [None])[0]
    context["budget_style"] = (answer_values.get("price_value") or [None])[0]
    signals = {
        "canonical_scores": {
            key: round(value, 4) for key, value in sorted(canonical_scores.items())
        },
        "answer_values": dict(sorted(answer_values.items())),
        "routing_state": dict(routing_state or {}),
    }
    return DeterministicAdaptiveProfile(
        profile_summary=_profile_summary(context, answer_values),
        confidence=1.0,
        context=context,
        preferences=preferences,
        unresolved_attributes=unresolved,
        signals=signals,
    )


def _add_effects(
    scores: dict[str, float],
    evidence: dict[str, set[str]],
    effects: Any,
    evidence_id: str,
) -> None:
    for slug, points in effects:
        if slug not in _SUPPORTED_ATTRIBUTE_SLUGS:
            continue
        scores[slug] = scores.get(slug, 0.0) + points
        if evidence_id:
            evidence.setdefault(slug, set()).add(evidence_id)


def _effects_for(key: str, value: str) -> tuple[tuple[str, float], ...]:
    normalized = _normalize(value)
    if key == "travel_group":
        return ()
    if key == "destination_branch":
        return ()
    return _VALUE_EFFECTS.get(key, {}).get(normalized, ())


def _dimension_effects(dimensions: set[str]) -> tuple[tuple[str, float], ...]:
    effects: list[tuple[str, float]] = []
    for dimension in sorted(dimensions):
        effects.extend(_DIMENSION_EFFECTS.get(dimension, ()))
    return tuple(effects)


def _dimensions_for(question: Any) -> set[str]:
    dimensions = {
        _normalize(str(item))
        for item in (getattr(question, "semantic_dimensions", None) or [])
        if str(item).strip()
    }
    aliases = {
        "budget_sensitivity": "price",
        "value_expectation": "price",
        "travel_pace": "activities",
        "activity_level": "activities",
        "dining_preference": "food",
        "urban_location": "location",
        "room": "room_quality",
    }
    return {
        aliases.get(item, item)
        for item in dimensions
        if item in aliases or item in _CANONICAL_DIMENSIONS
    }


def _canonical_question_key(question: Any, answer: dict[str, Any]) -> str | None:
    metadata = getattr(question, "generation_metadata", None) or {}
    raw_key = str(metadata.get("question_key") or "")
    if raw_key in CANONICAL_ADAPTIVE_QUESTION_KEYS:
        return raw_key
    if raw_key in {"group_type", "travel_group"}:
        return "travel_group"
    if raw_key == "destination_type":
        return "destination_branch"
    dimensions = _dimensions_for(question)
    value = _answer_values(answer)
    if dimensions == {"price"}:
        return "price_value"
    if dimensions == {"activities"}:
        return "travel_pace"
    if dimensions == {"food"}:
        return "food_preference"
    if dimensions == {"location"} and value:
        return "destination_detail"
    if dimensions == {"couple_style"} and value:
        return "couple_style"
    if dimensions == {"family_needs"}:
        return "family_needs"
    if dimensions == {"friends_vibe"}:
        return "friends_vibe"
    return None


def _answer_values(answer: dict[str, Any]) -> list[str]:
    selected = answer.get("selected")
    values = selected if isinstance(selected, list) else [selected]
    if selected is None:
        values = [answer.get("value")]
    return [_normalize(str(value)) for value in values if value is not None and str(value).strip()]


def _normalize(value: str) -> str:
    return value.strip().casefold().replace("-", "_").replace(" ", "_")


def _profile_context(location: dict[str, Any]) -> dict[str, Any]:
    return {
        "destination_raw": location.get("raw_input"),
        "destination_normalized_text": location.get("normalized_location_text"),
        "destination_country_id": location.get("country_id"),
        "destination_region_id": location.get("region_id"),
        "destination_city_id": location.get("city_id"),
        "destination_display_name": location.get("display_name"),
        "destination_resolution_status": location.get("resolution_status"),
        "destination_resolution_confidence": location.get("resolution_confidence"),
        "destination_type": None,
        "group_type": None,
        "travel_pace": None,
        "budget_style": None,
    }


def _profile_summary(context: dict[str, Any], values: dict[str, list[str]]) -> str:
    location = (
        context.get("destination_raw")
        or context.get("destination_display_name")
        or "your destination"
    )
    group = _display(
        values.get("travel_group", []),
        {"couple": "Couple", "family": "Family", "friends": "Friends", "solo": "Solo"},
    )
    price = _display(
        values.get("price_value", []),
        {
            "budget_conscious": "budget-conscious",
            "balanced_value": "balanced value",
            "premium_quality": "premium quality",
            "luxury_experience": "luxury experience",
        },
    )
    pace = _display(
        values.get("travel_pace", []),
        {
            "active_packed": "active",
            "mixed_pace": "mixed",
            "mixed": "mixed",
            "restful": "restful",
            "restful_relaxed": "restful",
            "relaxed": "relaxed",
        },
    )
    food = _display(
        values.get("food_preference", []),
        {"eating_out": "eating out", "hotel_dining": "hotel dining", "mix": "mixed"},
    )
    couple_style = _display(
        values.get("couple_style", []),
        {
            "romance": "romance",
            "adventure": "adventure",
            "decompression": "decompression",
            "relaxation": "relaxation",
        },
    )
    destination = _display(
        values.get("destination_detail", [])
        + values.get("rural_isolation", [])
        + values.get("city_focus", []),
        {
            "peaceful_isolation": "peaceful surroundings",
            "peaceful_retreat": "peaceful surroundings",
            "scenery": "scenery",
            "outdoors": "outdoor activities",
        },
    )
    parts = [f"{group or 'Traveler'} trip to {location}"]
    if price:
        parts.append(f"with {price} preference")
    if pace:
        parts.append(f"{pace} travel pace")
    if food:
        parts.append(f"{food} dining style")
    if couple_style:
        parts.append(f"{couple_style} focus")
    if destination:
        parts.append(f"preference for {destination}")
    return (parts[0] + (" with " if len(parts) > 1 else "") + ", ".join(parts[1:]) + ".")[:1000]


def _display(values: list[str], labels: dict[str, str]) -> str | None:
    for value in values:
        if value in labels:
            return labels[value]
    return None

"""Pure parsing and deterministic rules for aggregate hotel sentiment."""

import hashlib
import math
import unicodedata
from dataclasses import dataclass

from app.enums import Sentiment
from app.exceptions import ImportValidationError


def normalize_taxonomy_key(value: str) -> str:
    """Normalize a dynamic taxonomy label into a stable lowercase key."""
    if not isinstance(value, str):
        raise ImportValidationError("INVALID_TAXONOMY_KEY")
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    key_parts: list[str] = []
    separator_pending = False
    for character in normalized:
        if character.isalnum():
            if separator_pending and key_parts:
                key_parts.append("-")
            key_parts.append(character)
            separator_pending = False
        else:
            separator_pending = True
    key = "".join(key_parts)
    if not key:
        raise ImportValidationError("INVALID_TAXONOMY_KEY")
    return key


def deterministic_category_code(normalized_key: str) -> str:
    """Return a stable database category code from a normalized taxonomy key."""
    code = normalized_key.upper().replace("-", "_")
    if len(code) > 64:
        digest = hashlib.sha256(normalized_key.encode("utf-8")).hexdigest()[:10]
        code = f"{code[:53]}_{digest}"
    return code


def deterministic_attribute_slug(
    category_key: str, attribute_key: str, used_slugs: set[str]
) -> str:
    """Choose a globally unique, stable attribute slug, bounded to 120 chars."""
    if attribute_key not in used_slugs:
        return attribute_key
    prefixed = f"{category_key}--{attribute_key}"
    if prefixed not in used_slugs:
        return prefixed
    digest = hashlib.sha256(f"{category_key}\0{attribute_key}".encode()).hexdigest()[:10]
    suffix = f"--{digest}"
    readable = prefixed[: 120 - len(suffix)].rstrip("-")
    return readable + suffix


def aggregate_score_0_5(positive: int, negative: int, total: int) -> float:
    """Map positive/negative mention balance to a 0–5 score."""
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
        raise ImportValidationError("INVALID_MENTION_COUNTS")
    return 2.5 + 2.5 * ((positive - negative) / total)


def aggregate_support_confidence(total: int) -> float:
    """Return support confidence normalized to ten analyzed mentions."""
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ImportValidationError("INVALID_MENTION_COUNTS")
    return min(1.0, math.log1p(total) / math.log(11))


@dataclass(frozen=True, slots=True)
class AttributeSentimentValue:
    """Parsed mention counts and canonical sentiment for one attribute."""

    sentiment: Sentiment
    total_mentions: int
    positive_mentions: int
    negative_mentions: int

    @property
    def neutral_mentions(self) -> int:
        return self.total_mentions - self.positive_mentions - self.negative_mentions

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "AttributeSentimentValue":
        sentiment_raw = payload.get("sentiment")
        if not isinstance(sentiment_raw, str):
            raise ImportValidationError("INVALID_SENTIMENT")
        try:
            sentiment = Sentiment(sentiment_raw.strip().upper())
        except ValueError as exc:
            raise ImportValidationError("INVALID_SENTIMENT") from exc
        values: dict[str, int] = {}
        for source_name, target_name in (
            ("mentions", "total_mentions"),
            ("positive_mentions", "positive_mentions"),
            ("negative_mentions", "negative_mentions"),
        ):
            value = payload.get(source_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ImportValidationError("INVALID_MENTION_COUNTS")
            values[target_name] = value
        if values["positive_mentions"] + values["negative_mentions"] > values["total_mentions"]:
            raise ImportValidationError(
                "Invalid mention counts: positive and negative mentions exceed total mention counts"
            )
        return cls(sentiment=sentiment, **values)


@dataclass(frozen=True, slots=True)
class HotelSentimentRecord:
    """Validated hotel-level aggregate sentiment record."""

    hotel_id: str | int
    analysis_mode: str
    analysis_version: str
    review_window: str
    has_recent_reviews: bool | None
    reviews_analyzed: int
    attributes: dict[str, object]

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "HotelSentimentRecord":
        hotel_id = payload.get("hotel_id")
        if isinstance(hotel_id, bool) or not isinstance(hotel_id, str | int):
            raise ImportValidationError("INVALID_HOTEL_ID")
        if isinstance(hotel_id, str) and not hotel_id.strip():
            raise ImportValidationError("INVALID_HOTEL_ID")
        analysis_mode = payload.get("analysis_mode")
        if not isinstance(analysis_mode, str) or not analysis_mode.strip():
            raise ImportValidationError("INVALID_SENTIMENT_PROVENANCE")

        provenance: dict[str, str] = {}
        for name in ("analysis_version", "review_window"):
            value = payload.get(name)
            if value is None:
                provenance[name] = "unspecified"
            elif not isinstance(value, str) or not value.strip():
                raise ImportValidationError("INVALID_SENTIMENT_PROVENANCE")
            else:
                provenance[name] = value.strip()
        has_recent_reviews = payload.get("has_recent_reviews")
        if has_recent_reviews is not None and not isinstance(has_recent_reviews, bool):
            raise ImportValidationError("INVALID_HAS_RECENT_REVIEWS")
        reviews_analyzed = payload.get("reviews_analyzed")
        if (
            isinstance(reviews_analyzed, bool)
            or not isinstance(reviews_analyzed, int)
            or reviews_analyzed < 0
        ):
            raise ImportValidationError("INVALID_REVIEWS_ANALYZED")
        attributes = payload.get("attributes")
        if not isinstance(attributes, dict):
            raise ImportValidationError("INVALID_ATTRIBUTES")
        return cls(
            hotel_id=hotel_id,
            analysis_mode=analysis_mode.strip(),
            **provenance,
            has_recent_reviews=has_recent_reviews,
            reviews_analyzed=reviews_analyzed,
            attributes=attributes,
        )

"""Hotel, image, amenity, and attribute DTOs."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.dto.common import DTO


class SourceSummary(DTO):
    """Provider-specific hotel summary."""

    code: str
    source_hotel_id: str
    source_url: str | None
    source_rating: float | None
    source_review_count: int | None
    source_rank: int | None
    source_rank_text: str | None


class HotelImageRead(DTO):
    """Hotel image response."""

    id: uuid.UUID
    image_url: str
    thumbnail_url: str | None
    image_category: str | None
    caption: str | None
    position: int
    is_primary: bool


class AmenityRead(DTO):
    """Factual hotel amenity response."""

    id: uuid.UUID
    name: str
    slug: str
    category: str | None
    is_available: bool


class HotelListItem(DTO):
    """Compact hotel search projection."""

    id: uuid.UUID
    name: str
    hotel_type: str | None
    city: str
    region: str
    country: str
    latitude: float
    longitude: float
    source_rating: float | None = None
    source_review_count: int | None = None
    primary_image_url: str | None = None
    distance_km: float | None = None


class HotelDetail(HotelListItem):
    """Complete canonical hotel detail projection."""

    address: str | None
    postal_code: str | None
    telephone: str | None
    images: list[HotelImageRead]
    amenities: list[AmenityRead]
    sources: list[SourceSummary]


class AttributeRead(DTO):
    """Attribute taxonomy response."""

    id: uuid.UUID
    category_id: uuid.UUID
    category_name: str
    name: str
    slug: str
    description: str | None


class HotelAttributeRead(DTO):
    """Precomputed hotel attribute score."""

    attribute_id: uuid.UUID
    name: str
    slug: str
    mention_count: int
    positive_mentions: int
    negative_mentions: int
    neutral_mentions: int
    score_5: float | None
    score_100: float | None
    z_score: float | None
    relative_score_5: float | None
    confidence_score: float
    calculated_at: datetime


class HotelSearchFilters(DTO):
    """Validated hotel query filters."""

    country_id: uuid.UUID | None = None
    region_id: uuid.UUID | None = None
    city_id: uuid.UUID | None = None
    hotel_type: str | None = None
    min_rating: float | None = None
    max_rating: float | None = None
    attribute_slugs: list[str] = Field(default_factory=list, max_length=24)
    sort: Literal["rating_desc", "review_count_desc", "name_asc", "id_asc"] = "rating_desc"

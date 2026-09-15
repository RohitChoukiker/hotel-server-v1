"""Review response and filter DTOs."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.dto.common import DTO
from app.enums import Sentiment


class ReviewImageRead(DTO):
    """Review image response."""

    id: uuid.UUID
    image_url: str
    thumbnail_url: str | None
    position: int


class ReviewRead(DTO):
    """Provider-neutral review response."""

    id: uuid.UUID
    hotel_id: uuid.UUID
    source_code: str
    source_review_id: str
    source_rating: float
    source_rating_scale: float
    normalized_rating_5: float
    title: str | None
    review_text: str | None
    review_date: date | None
    reviewer_name: str | None
    trip_type: str | None
    language: str | None
    source_url: str | None
    scraped_at: datetime
    images: list[ReviewImageRead] = Field(default_factory=list)


class ReviewFilters(DTO):
    """Validated large review-list filters."""

    rating: float | None = None
    trip_type: str | None = None
    source: str | None = None
    attribute: str | None = None
    sentiment: Sentiment | None = None
    date_from: date | None = None
    date_to: date | None = None
    sort: Literal["newest", "oldest", "rating_high", "rating_low"] = "newest"

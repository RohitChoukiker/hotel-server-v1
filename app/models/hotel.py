"""Canonical hotel, source, amenity, image, and review models."""

import uuid
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Hotel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Provider-neutral canonical hotel."""

    __tablename__ = "hotels"
    __table_args__ = (
        Index("ix_hotels_country_id", "country_id"),
        Index("ix_hotels_region_id", "region_id"),
        Index("ix_hotels_city_id", "city_id"),
        Index("ix_hotels_geo_location", "geo_location", postgresql_using="gist"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
    )

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    country_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("countries.id", ondelete="RESTRICT"), nullable=False
    )
    region_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regions.id", ondelete="RESTRICT"), nullable=False
    )
    city_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("localities.id", ondelete="SET NULL")
    )
    hotel_type: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(String(32))
    telephone: Mapped[str | None] = mapped_column(String(64))
    latitude: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    geo_location: Mapped[Any] = mapped_column(Geography("POINT", srid=4326), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Hotel/review upstream provider."""

    __tablename__ = "data_sources"

    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class HotelSourceMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Exact upstream-to-canonical hotel mapping."""

    __tablename__ = "hotel_source_mappings"
    __table_args__ = (
        UniqueConstraint("source_id", "source_hotel_id"),
        Index("ix_hotel_source_mappings_hotel", "hotel_id"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False
    )
    source_hotel_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_rating: Mapped[float | None] = mapped_column(Numeric(6, 3))
    source_review_count: Mapped[int | None]
    source_rank: Mapped[int | None]
    source_rank_text: Mapped[str | None] = mapped_column(String(300))
    raw_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class HotelImage(UUIDPrimaryKeyMixin, Base):
    """Normalized hotel image."""

    __tablename__ = "hotel_images"
    __table_args__ = (
        Index("ix_hotel_images_hotel_position", "hotel_id", "position"),
        UniqueConstraint("hotel_id", "image_url"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    image_category: Mapped[str | None] = mapped_column(String(32))
    caption: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(nullable=False, default=0)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Amenity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Factual amenity taxonomy entry."""

    __tablename__ = "amenities"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class HotelAmenity(TimestampMixin, Base):
    """Factual hotel amenity availability."""

    __tablename__ = "hotel_amenities"

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hotels.id", ondelete="CASCADE"), primary_key=True
    )
    amenity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("amenities.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Review(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Provider review with preserved source identity and rating."""

    __tablename__ = "reviews"
    __table_args__ = (
        UniqueConstraint("source_id", "source_review_id"),
        Index("ix_reviews_hotel_date", "hotel_id", "review_date"),
        CheckConstraint("source_rating_scale > 0", name="source_scale_positive"),
        CheckConstraint(
            "normalized_rating_5 BETWEEN 0 AND 5", name="normalized_rating_range"
        ),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="RESTRICT"), nullable=False
    )
    source_review_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_rating: Mapped[float] = mapped_column(Numeric(7, 3), nullable=False)
    source_rating_scale: Mapped[float] = mapped_column(Numeric(7, 3), nullable=False)
    normalized_rating_5: Mapped[float] = mapped_column(Numeric(5, 3), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    review_text: Mapped[str | None] = mapped_column(Text)
    review_date: Mapped[date | None] = mapped_column(Date)
    reviewer_name: Mapped[str | None] = mapped_column(String(240))
    trip_type: Mapped[str | None] = mapped_column(String(80))
    language: Mapped[str | None] = mapped_column(String(12))
    source_url: Mapped[str | None] = mapped_column(Text)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewImage(UUIDPrimaryKeyMixin, Base):
    """One normalized image attached to a review."""

    __tablename__ = "review_images"
    __table_args__ = (
        Index("ix_review_images_review_position", "review_id", "position"),
        UniqueConstraint("review_id", "image_url"),
    )

    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

"""Attribute extraction, scoring, and normalization models."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AttributeCategory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Top-level score category."""

    __tablename__ = "attribute_categories"

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    display_order: Mapped[int] = mapped_column(nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Attribute(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Review-derived scoring dimension."""

    __tablename__ = "attributes"
    __table_args__ = (UniqueConstraint("category_id", "slug"),)

    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attribute_categories.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    value_type: Mapped[str] = mapped_column(String(40), nullable=False, default="SCORE")
    display_order: Mapped[int] = mapped_column(nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AttributeAlias(UUIDPrimaryKeyMixin, Base):
    """Phrase alias used for multilingual structured extraction."""

    __tablename__ = "attribute_aliases"
    __table_args__ = (UniqueConstraint("attribute_id", "alias", "language"),)

    attribute_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attributes.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(160), nullable=False)
    language: Mapped[str] = mapped_column(String(12), nullable=False, default="en")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AlgorithmVersion(UUIDPrimaryKeyMixin, Base):
    """Immutable scoring algorithm configuration."""

    __tablename__ = "algorithm_versions"
    __table_args__ = (UniqueConstraint("name", "version"),)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewAttributeMention(UUIDPrimaryKeyMixin, Base):
    """Structured attribute evidence extracted from one review."""

    __tablename__ = "review_attribute_mentions"
    __table_args__ = (
        Index("ix_review_attribute_mentions_review", "review_id"),
        Index("ix_review_attribute_mentions_attribute", "attribute_id"),
        UniqueConstraint("review_id", "attribute_id", "evidence_text", "algorithm_version_id"),
    )

    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False
    )
    attribute_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attributes.id"), nullable=False
    )
    sentiment: Mapped[str] = mapped_column(String(16), nullable=False)
    sentiment_value: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    review_rating_used: Mapped[float] = mapped_column(Numeric(5, 3), nullable=False)
    calculated_contribution: Mapped[float] = mapped_column(Numeric(5, 3), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    algorithm_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("algorithm_versions.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewProcessingStatus(Base):
    """Idempotent per-review extraction state, including zero-mention reviews."""

    __tablename__ = "review_processing_statuses"
    __table_args__ = (Index("ix_review_processing_status", "status", "processed_at"),)

    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reviews.id", ondelete="CASCADE"),
        primary_key=True,
    )
    algorithm_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("algorithm_versions.id"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=1)
    mention_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HotelAttributeScore(Base):
    """Precomputed aggregate score for one hotel and attribute."""

    __tablename__ = "hotel_attribute_scores"
    __table_args__ = (
        Index("ix_hotel_attribute_scores_hotel", "hotel_id"),
        Index("ix_hotel_attribute_scores_attribute_relative", "attribute_id", "relative_score_5"),
    )

    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hotels.id", ondelete="CASCADE"), primary_key=True
    )
    attribute_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attributes.id"), primary_key=True
    )
    algorithm_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("algorithm_versions.id"), primary_key=True
    )
    mention_count: Mapped[int] = mapped_column(nullable=False)
    positive_mentions: Mapped[int] = mapped_column(nullable=False)
    negative_mentions: Mapped[int] = mapped_column(nullable=False)
    neutral_mentions: Mapped[int] = mapped_column(nullable=False)
    raw_score: Mapped[float | None] = mapped_column(Numeric(7, 4))
    score_5: Mapped[float | None] = mapped_column(Numeric(7, 4))
    score_100: Mapped[float | None] = mapped_column(Numeric(7, 3))
    mean: Mapped[float | None] = mapped_column(Numeric(7, 4))
    standard_deviation: Mapped[float | None] = mapped_column(Numeric(7, 4))
    z_score: Mapped[float | None] = mapped_column(Numeric(8, 4))
    relative_score_5: Mapped[float | None] = mapped_column(Numeric(7, 4))
    confidence_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NormalizationRun(UUIDPrimaryKeyMixin, Base):
    """Auditable bell-curve population calculation."""

    __tablename__ = "normalization_runs"
    __table_args__ = (Index("ix_normalization_scope_attribute", "scope_type", "attribute_id"),)

    attribute_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attributes.id"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)
    country_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("countries.id")
    )
    region_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regions.id")
    )
    city_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cities.id"))
    hotel_type: Mapped[str | None] = mapped_column(String(100))
    population_size: Mapped[int] = mapped_column(nullable=False)
    mean_score: Mapped[float] = mapped_column(Numeric(7, 4), nullable=False)
    standard_deviation: Mapped[float] = mapped_column(Numeric(7, 4), nullable=False)
    algorithm_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("algorithm_versions.id"), nullable=False
    )
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

"""Trips, recommendations, and conversations."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Trip(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """User-owned trip context."""

    __tablename__ = "trips"
    __table_args__ = (Index("ix_trips_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    destination_country_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("countries.id"), nullable=False
    )
    destination_region_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regions.id")
    )
    destination_city_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cities.id")
    )
    check_in_date: Mapped[date | None] = mapped_column(Date)
    check_out_date: Mapped[date | None] = mapped_column(Date)
    trip_purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    party_type: Mapped[str | None] = mapped_column(String(64))
    adult_count: Mapped[int] = mapped_column(nullable=False, default=1)
    child_count: Mapped[int] = mapped_column(nullable=False, default=0)
    room_count: Mapped[int] = mapped_column(nullable=False, default=1)
    budget_min: Mapped[float | None] = mapped_column(Numeric(12, 2))
    budget_max: Mapped[float | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DRAFT")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TripAttributePreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Trip-scoped preference override."""

    __tablename__ = "trip_attribute_preferences"
    __table_args__ = (UniqueConstraint("trip_id", "attribute_id"),)

    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    attribute_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attributes.id"), nullable=False
    )
    weight: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    minimum_required_score: Mapped[float | None] = mapped_column(Numeric(5, 3))
    is_mandatory: Mapped[bool] = mapped_column(nullable=False, default=False)


class RecommendationRun(UUIDPrimaryKeyMixin, Base):
    """Immutable inputs and summary for one live ranking."""

    __tablename__ = "recommendation_runs"
    __table_args__ = (Index("ix_recommendation_runs_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    algorithm_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("algorithm_versions.id"), nullable=False
    )
    preference_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    candidate_count: Mapped[int] = mapped_column(nullable=False)
    result_count: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HotelRecommendation(Base):
    """Ranked hotel result and explainability payload."""

    __tablename__ = "hotel_recommendations"
    __table_args__ = (
        UniqueConstraint("recommendation_run_id", "hotel_id"),
        Index("ix_hotel_recommendations_run_rank", "recommendation_run_id", "rank"),
    )

    recommendation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recommendation_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hotels.id", ondelete="CASCADE"), primary_key=True
    )
    rank: Mapped[int] = mapped_column(nullable=False)
    personalized_rating: Mapped[float] = mapped_column(Numeric(5, 3), nullable=False)
    match_score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    coverage_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ChatConversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """User-owned assistant conversation."""

    __tablename__ = "chat_conversations"
    __table_args__ = (Index("ix_chat_conversations_user_last", "user_id", "last_message_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    trip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="SET NULL")
    )
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatMessage(UUIDPrimaryKeyMixin, Base):
    """One visible chat message; hidden reasoning is never persisted."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_conversation_created", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    structured_intent: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

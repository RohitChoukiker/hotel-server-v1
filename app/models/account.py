"""Account, authentication, and onboarding persistence models."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Application account."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), unique=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="USER")
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Hashed rotating refresh-token record."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_family", "user_id", "family_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("refresh_tokens.id", ondelete="SET NULL")
    )
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip_address: Mapped[str | None] = mapped_column(String(64))


class OnboardingQuestionSet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Versioned onboarding questionnaire."""

    __tablename__ = "onboarding_question_sets"
    __table_args__ = (UniqueConstraint("name", "version"),)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class OnboardingQuestion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single ordered onboarding question."""

    __tablename__ = "onboarding_questions"
    __table_args__ = (UniqueConstraint("question_set_id", "position"),)

    question_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_question_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    answer_type: Mapped[str] = mapped_column(String(32), nullable=False)
    options: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    position: Mapped[int] = mapped_column(nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class OnboardingSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One user's questionnaire attempt."""

    __tablename__ = "onboarding_sessions"
    __table_args__ = (Index("ix_onboarding_sessions_user_status", "user_id", "status"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    question_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboarding_question_sets.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="IN_PROGRESS")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OnboardingAnswer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Structured or free-form answer to one onboarding question."""

    __tablename__ = "onboarding_answers"
    __table_args__ = (UniqueConstraint("session_id", "question_id"),)

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboarding_sessions.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboarding_questions.id"), nullable=False
    )
    answer: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    interpreted_preferences: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)


class UserPreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Attribute preference retained by provenance."""

    __tablename__ = "user_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "attribute_id", "preference_source"),
        Index("ix_user_preferences_user", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    attribute_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attributes.id", ondelete="CASCADE"), nullable=False
    )
    importance_weight: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    preferred_value: Mapped[str | None] = mapped_column(String(200))
    preference_source: Mapped[str] = mapped_column(String(32), nullable=False)


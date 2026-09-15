"""Import, scraper, data-quality, settings, and audit models."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ImportJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Resumable CSV import state and statistics."""

    __tablename__ = "import_jobs"
    __table_args__ = (Index("ix_import_jobs_status_created", "status", "created_at"),)

    status: Mapped[str] = mapped_column(String(24), nullable=False)
    import_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id"), nullable=False
    )
    country_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("countries.id")
    )
    region_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regions.id")
    )
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    records_read: Mapped[int] = mapped_column(nullable=False, default=0)
    inserted: Mapped[int] = mapped_column(nullable=False, default=0)
    updated: Mapped[int] = mapped_column(nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(nullable=False, default=0)
    failed: Mapped[int] = mapped_column(nullable=False, default=0)
    progress: Mapped[float] = mapped_column(nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ScrapeRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One source scraping operation."""

    __tablename__ = "scrape_runs"
    __table_args__ = (Index("ix_scrape_runs_status_created", "status", "created_at"),)

    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_sources.id"))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    processed_count: Mapped[int] = mapped_column(nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScrapeCheckpoint(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Opaque resumable state for a scrape run partition."""

    __tablename__ = "scrape_checkpoints"
    __table_args__ = (UniqueConstraint("scrape_run_id", "partition_key"),)

    scrape_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scrape_runs.id", ondelete="CASCADE"), nullable=False
    )
    partition_key: Mapped[str] = mapped_column(String(240), nullable=False)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class HotelReviewFetchStatus(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-source-hotel review-fetch completion state."""

    __tablename__ = "hotel_review_fetch_status"
    __table_args__ = (UniqueConstraint("source_id", "source_hotel_id"),)

    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_sources.id"))
    source_hotel_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    last_offset: Mapped[int] = mapped_column(nullable=False, default=0)
    reviews_fetched: Mapped[int] = mapped_column(nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class ScrapeFailure(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Retryable scraper failure with bounded retry state."""

    __tablename__ = "scrape_failures"
    __table_args__ = (Index("ix_scrape_failures_run_resolved", "scrape_run_id", "resolved_at"),)

    scrape_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scrape_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_entity_id: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str] = mapped_column(String(100), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataQualityIssue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persisted invalid or suspicious source record."""

    __tablename__ = "data_quality_issues"
    __table_args__ = (Index("ix_data_quality_type_resolved", "issue_type", "resolved_at"),)

    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id")
    )
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_jobs.id", ondelete="SET NULL")
    )
    entity_type: Mapped[str | None] = mapped_column(String(64))
    source_entity_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Append-only audit trail for privileged actions."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_actor_created", "actor_user_id", "created_at"),)

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(200))
    metadata_payload: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SystemSetting(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Runtime-safe non-secret system setting."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

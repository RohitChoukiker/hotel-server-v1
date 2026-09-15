"""Administrator and data-operator DTOs."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.dto.common import DTO


class JobAccepted(DTO):
    """Asynchronous job acceptance response."""

    job_id: uuid.UUID
    status: str


class ImportJobRead(DTO):
    """CSV import status and statistics."""

    id: uuid.UUID
    status: str
    import_type: str
    source_id: uuid.UUID
    country_id: uuid.UUID | None
    region_id: uuid.UUID | None
    file_name: str
    records_read: int
    inserted: int
    updated: int
    skipped: int
    failed: int
    progress: float
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    checkpoint: dict[str, Any] | None


class ScrapeRunRead(DTO):
    """Scraper run status."""

    id: uuid.UUID
    source_id: uuid.UUID
    status: str
    scope: dict[str, Any]
    processed_count: int
    success_count: int
    failure_count: int
    started_at: datetime | None
    completed_at: datetime | None


class ScrapeRunCreate(DTO):
    """Explicit, bounded scraper run request."""

    source_code: str = "TRIPADVISOR"
    geo_id: str = Field(min_length=1, max_length=100)
    start_offset: int = Field(default=0, ge=0)
    page_size: int = Field(default=30, ge=1, le=100)
    max_pages: int = Field(default=1, ge=1, le=10_000)
    extra_variables: dict[str, Any] = Field(default_factory=dict)


class ScrapeFailureRead(DTO):
    """One retryable scraper failure."""

    id: uuid.UUID
    scrape_run_id: uuid.UUID
    source_entity_id: str | None
    error_code: str
    error_message: str
    retry_count: int
    next_retry_at: datetime | None
    resolved_at: datetime | None


class DataQualityIssueRead(DTO):
    """Persisted data-quality issue."""

    id: uuid.UUID
    issue_type: str
    severity: str
    entity_type: str | None
    source_entity_id: str | None
    details: dict[str, Any]
    resolved_at: datetime | None
    created_at: datetime


class AlgorithmVersionRead(DTO):
    """Scoring algorithm metadata."""

    id: uuid.UUID
    name: str
    version: int
    description: str | None
    configuration: dict[str, Any]
    is_active: bool


class DashboardRead(DTO):
    """Operational aggregate counters."""

    users: int
    active_hotels: int
    reviews: int
    unresolved_quality_issues: int
    running_imports: int
    failed_scrapes: int


class SystemSettingWrite(DTO):
    """Non-secret runtime setting payload."""

    value: dict[str, Any]


class SystemSettingRead(SystemSettingWrite):
    """Persisted non-secret runtime setting."""

    key: str
    updated_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

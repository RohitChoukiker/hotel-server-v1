"""Trip and trip-preference DTOs."""

import uuid
from datetime import date, datetime

from pydantic import Field, model_validator

from app.dto.common import DTO
from app.enums import TripPurpose, TripStatus


class TripWrite(DTO):
    """Trip creation or replacement payload."""

    destination_country_id: uuid.UUID
    destination_region_id: uuid.UUID | None = None
    destination_city_id: uuid.UUID | None = None
    check_in_date: date | None = None
    check_out_date: date | None = None
    trip_purpose: TripPurpose
    party_type: str | None = Field(default=None, max_length=64)
    adult_count: int = Field(default=1, ge=1, le=30)
    child_count: int = Field(default=0, ge=0, le=30)
    room_count: int = Field(default=1, ge=1, le=20)
    budget_min: float | None = Field(default=None, ge=0)
    budget_max: float | None = Field(default=None, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    status: TripStatus = TripStatus.DRAFT

    @model_validator(mode="after")
    def validate_ranges(self) -> "TripWrite":
        """Validate dates and budget ranges."""
        if self.check_in_date and self.check_out_date and self.check_out_date <= self.check_in_date:
            raise ValueError("check_out_date must be after check_in_date")
        if self.budget_min is not None and self.budget_max is not None:
            if self.budget_max < self.budget_min:
                raise ValueError("budget_max must be greater than or equal to budget_min")
        return self


class TripRead(TripWrite):
    """Persisted user trip response."""

    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class TripPreferenceWrite(DTO):
    """One trip-specific attribute override."""

    attribute_id: uuid.UUID
    weight: float = Field(ge=0.0, le=1.0)
    minimum_required_score: float | None = Field(default=None, ge=0.0, le=5.0)
    is_mandatory: bool = False


class TripPreferencesReplace(DTO):
    """Atomic trip-preference replacement."""

    preferences: list[TripPreferenceWrite] = Field(max_length=100)


class TripPreferenceRead(TripPreferenceWrite):
    """Persisted trip preference."""

    id: uuid.UUID
    trip_id: uuid.UUID


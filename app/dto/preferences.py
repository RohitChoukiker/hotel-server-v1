"""Preference API DTOs."""

import uuid

from pydantic import Field

from app.dto.common import DTO
from app.enums import PreferenceSource


class PreferenceWrite(DTO):
    """One manually controlled preference."""

    attribute_id: uuid.UUID
    importance_weight: float = Field(ge=0.0, le=1.0)
    preferred_value: str | None = Field(default=None, max_length=200)


class PreferencesReplace(DTO):
    """Atomic replacement of manual preferences."""

    preferences: list[PreferenceWrite] = Field(min_length=1, max_length=100)


class PreferenceRead(DTO):
    """Effective preference projection."""

    id: uuid.UUID | None
    attribute_id: uuid.UUID
    attribute_name: str
    attribute_slug: str
    importance_weight: float
    preferred_value: str | None
    preference_source: PreferenceSource


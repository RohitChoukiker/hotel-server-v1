"""Geographical response DTOs."""

import uuid

from pydantic import Field

from app.dto.common import DTO
from app.enums import RegionType


class CountryRead(DTO):
    """Country response."""

    id: uuid.UUID
    iso2_code: str
    iso3_code: str
    name: str


class RegionRead(DTO):
    """Region response."""

    id: uuid.UUID
    country_id: uuid.UUID
    name: str
    code: str | None
    region_type: RegionType


class CityRead(DTO):
    """City response."""

    id: uuid.UUID
    region_id: uuid.UUID
    name: str
    latitude: float | None
    longitude: float | None
    timezone: str | None


class CityWrite(DTO):
    """Data-operator canonical city payload."""

    region_id: uuid.UUID
    name: str = Field(min_length=1, max_length=160)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    timezone: str | None = Field(default=None, max_length=64)


class LocationSearchResult(DTO):
    """Polymorphic location search result."""

    id: uuid.UUID
    type: str
    name: str
    region_name: str | None = None
    country_name: str

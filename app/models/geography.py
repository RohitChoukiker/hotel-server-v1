"""International-ready geographical persistence models."""

import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Country(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """ISO country record."""

    __tablename__ = "countries"

    iso2_code: Mapped[str] = mapped_column(String(2), unique=True, nullable=False)
    iso3_code: Mapped[str] = mapped_column(String(3), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Region(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """State, province, territory, or equivalent region."""

    __tablename__ = "regions"
    __table_args__ = (
        Index("ix_regions_country_name", "country_id", "name", unique=True),
    )

    country_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("countries.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str | None] = mapped_column(String(20))
    region_type: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class City(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """City scoped to one administrative region."""

    __tablename__ = "cities"
    __table_args__ = (
        Index("ix_cities_region_name", "region_id", "name", unique=True),
        CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90", name="latitude_range"
        ),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180", name="longitude_range"
        ),
    )

    region_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("regions.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]
    timezone: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Locality(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Optional neighborhood/locality beneath a city."""

    __tablename__ = "localities"
    __table_args__ = (
        Index("ix_localities_city_name", "city_id", "name", unique=True),
        CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90", name="latitude_range"
        ),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180", name="longitude_range"
        ),
    )

    city_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cities.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    location_type: Mapped[str | None] = mapped_column(String(64))
    latitude: Mapped[float | None]
    longitude: Mapped[float | None]


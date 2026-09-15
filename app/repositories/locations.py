"""Geography persistence and validation queries."""

import uuid

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import City, Country, Region


class LocationRepository:
    """Read and validate international geography."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_countries(self) -> list[Country]:
        """Return active countries alphabetically."""
        return list(
            (
                await self._session.scalars(
                    select(Country).where(Country.is_active).order_by(Country.name)
                )
            ).all()
        )

    async def list_regions(self, country_id: uuid.UUID) -> list[Region]:
        """Return active regions in a country."""
        return list(
            (
                await self._session.scalars(
                    select(Region)
                    .where(Region.country_id == country_id, Region.is_active)
                    .order_by(Region.name)
                )
            ).all()
        )

    async def list_cities(self, region_id: uuid.UUID) -> list[City]:
        """Return active cities in a region."""
        return list(
            (
                await self._session.scalars(
                    select(City)
                    .where(City.region_id == region_id, City.is_active)
                    .order_by(City.name)
                )
            ).all()
        )

    async def validate_hierarchy(
        self,
        country_id: uuid.UUID,
        region_id: uuid.UUID | None,
        city_id: uuid.UUID | None,
    ) -> bool:
        """Verify that supplied destination members belong to each other."""
        country_exists = await self._session.scalar(
            select(func.count())
            .select_from(Country)
            .where(Country.id == country_id, Country.is_active)
        )
        if not country_exists:
            return False
        if region_id:
            region_exists = await self._session.scalar(
                select(func.count())
                .select_from(Region)
                .where(Region.id == region_id, Region.country_id == country_id, Region.is_active)
            )
            if not region_exists:
                return False
        if city_id:
            if not region_id:
                return False
            city_exists = await self._session.scalar(
                select(func.count())
                .select_from(City)
                .where(City.id == city_id, City.region_id == region_id, City.is_active)
            )
            if not city_exists:
                return False
        return True

    async def search(self, query_text: str, limit: int) -> list[dict[str, object]]:
        """Search countries, regions, and cities with fully qualified labels."""
        pattern = f"%{query_text}%"
        countries = select(
            Country.id.label("id"),
            literal("COUNTRY").label("type"),
            Country.name.label("name"),
            literal(None).label("region_name"),
            Country.name.label("country_name"),
        ).where(Country.name.ilike(pattern), Country.is_active)
        regions = (
            select(
                Region.id,
                literal("REGION"),
                Region.name,
                literal(None),
                Country.name,
            )
            .join(Country, Country.id == Region.country_id)
            .where(Region.name.ilike(pattern), Region.is_active)
        )
        cities = (
            select(City.id, literal("CITY"), City.name, Region.name, Country.name)
            .join(Region, Region.id == City.region_id)
            .join(Country, Country.id == Region.country_id)
            .where(City.name.ilike(pattern), City.is_active)
        )
        rows = (
            await self._session.execute(
                union_all(countries, regions, cities).limit(limit)
            )
        ).all()
        return [dict(row._mapping) for row in rows]

    async def find_country_by_iso2(self, iso2: str) -> Country | None:
        """Find a country by ISO-2 code."""
        return await self._session.scalar(select(Country).where(Country.iso2_code == iso2.upper()))

    async def find_region(self, country_id: uuid.UUID, name: str) -> Region | None:
        """Find a region by country and case-insensitive name."""
        return await self._session.scalar(
            select(Region).where(
                Region.country_id == country_id, func.lower(Region.name) == name.casefold()
            )
        )

    async def find_city(self, region_id: uuid.UUID, name: str) -> City | None:
        """Find a city only within the explicit region."""
        return await self._session.scalar(
            select(City).where(
                City.region_id == region_id,
                func.lower(City.name) == name.casefold(),
            )
        )

    async def get_region(self, region_id: uuid.UUID) -> Region | None:
        """Return one active canonical region."""
        return await self._session.scalar(
            select(Region).where(Region.id == region_id, Region.is_active)
        )

    async def upsert_city(
        self,
        region_id: uuid.UUID,
        name: str,
        latitude: float | None,
        longitude: float | None,
        timezone: str | None,
    ) -> City:
        """Create or update a city by case-insensitive regional identity."""
        city = await self.find_city(region_id, name)
        if city is None:
            city = City(region_id=region_id, name=name, is_active=True)
            self._session.add(city)
        city.latitude = latitude
        city.longitude = longitude
        city.timezone = timezone
        city.is_active = True
        await self._session.flush()
        return city

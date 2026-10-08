"""Geography persistence and validation queries."""

import uuid

from sqlalchemy import func, literal, or_, select, union_all
from sqlalchemy.dialects.postgresql import insert
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

    async def resolve_text(self, raw_text: str) -> dict[str, object] | None:
        """Resolve comma-separated destination text against canonical geography."""
        tokens = tuple(
            dict.fromkeys(
                token.strip().casefold()
                for token in raw_text.split(",")
                if token.strip()
            )
        )
        if not tokens:
            return None
        rows = (
            await self._session.execute(
                select(City, Region, Country)
                .join(Region, Region.id == City.region_id)
                .join(Country, Country.id == Region.country_id)
                .where(
                    City.is_active,
                    Region.is_active,
                    Country.is_active,
                    or_(
                        func.lower(func.btrim(City.name)).in_(tokens),
                        func.lower(func.btrim(Region.name)).in_(tokens),
                        func.lower(func.btrim(Country.name)).in_(tokens),
                    ),
                )
            )
        ).all()
        if not rows:
            return None

        def score(row: tuple[City, Region, Country]) -> tuple[int, int]:
            city, region, country = row
            city_match = int(city.name.strip().casefold() in tokens)
            region_match = int(region.name.strip().casefold() in tokens)
            country_match = int(country.name.strip().casefold() in tokens)
            return (city_match * 4 + region_match * 2 + country_match, city_match)

        city, region, country = max(rows, key=score)
        city_match = city.name.strip().casefold() in tokens
        region_match = region.name.strip().casefold() in tokens
        parts = (
            ([city.name] if city_match else [])
            + ([region.name] if region_match or city_match else [])
            + [country.name]
        )
        match_score = score((city, region, country))[0]
        return {
            "country_id": country.id,
            "region_id": region.id if region_match or city_match else None,
            "city_id": city.id if city_match else None,
            "display_name": ", ".join(parts),
            "normalized_location_text": ", ".join(parts).casefold(),
            "resolution_status": "resolved",
            "resolution_confidence": (
                1.0 if match_score >= 6 else 0.9 if city_match else 0.8 if region_match else 0.7
            ),
        }

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
        """Find a normalized city name only within the explicit region."""
        normalized_name = name.strip()
        return await self._session.scalar(
            select(City).where(
                City.region_id == region_id,
                func.lower(func.btrim(City.name)) == func.lower(normalized_name),
            )
        )

    async def get_or_create_city(
        self, region_id: uuid.UUID, name: str
    ) -> tuple[City, bool]:
        """Resolve or atomically create one normalized city within a region."""
        normalized_name = name.strip()
        city = await self.find_city(region_id, normalized_name)
        if city is not None:
            return city, False

        city_id = uuid.uuid4()
        statement = (
            insert(City)
            .values(
                id=city_id,
                region_id=region_id,
                name=normalized_name,
                latitude=None,
                longitude=None,
                timezone=None,
                is_active=True,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    City.region_id,
                    func.lower(func.btrim(City.name)),
                ]
            )
            .returning(City.id)
        )
        inserted_id = await self._session.scalar(statement)
        city = await self.find_city(region_id, normalized_name)
        if city is None:
            raise RuntimeError("City insert conflict did not resolve to a city")
        return city, inserted_id is not None

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

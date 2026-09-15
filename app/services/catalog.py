"""Location, hotel, review, and attribute read services."""

import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.cache import Cache
from app.common.logging import get_logger
from app.common.pagination import (
    InvalidCursorError,
    cursor_date,
    cursor_float,
    cursor_int,
    cursor_uuid,
    decode_cursor,
    encode_cursor,
)
from app.dto.common import Pagination
from app.dto.hotels import (
    AmenityRead,
    AttributeRead,
    HotelAttributeRead,
    HotelDetail,
    HotelImageRead,
    HotelListItem,
    HotelSearchFilters,
    SourceSummary,
)
from app.dto.locations import CityRead, CountryRead, LocationSearchResult, RegionRead
from app.dto.reviews import ReviewFilters, ReviewImageRead, ReviewRead
from app.exceptions import HotelNotFoundError, ImportValidationError, ReviewNotFoundError
from app.repositories.catalog import AttributeRepository, HotelRepository, ReviewRepository
from app.repositories.locations import LocationRepository


def _normalize_numeric(value: Any) -> Any:
    """Convert database Decimal values for strict DTO validation."""
    return float(value) if isinstance(value, Decimal) else value


def _row_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize numeric fields in a row mapping."""
    return {key: _normalize_numeric(value) for key, value in row.items()}


class LocationService:
    """Expose validated hierarchical geography."""

    def __init__(self, session: AsyncSession, cache: Cache | None = None) -> None:
        self._repository = LocationRepository(session)
        self._cache = cache

    async def countries(self) -> list[CountryRead]:
        """List active countries."""
        cached = await self._cached("locations:countries:v1")
        if cached is not None:
            return [CountryRead.model_validate(item) for item in cached]
        items = [
            CountryRead.model_validate(item)
            for item in await self._repository.list_countries()
        ]
        await self._store("locations:countries:v1", items, 86400)
        return items

    async def regions(self, country_id: uuid.UUID) -> list[RegionRead]:
        """List regions within one country."""
        key = f"locations:country:{country_id}:regions:v1"
        cached = await self._cached(key)
        if cached is not None:
            return [RegionRead.model_validate(item) for item in cached]
        items = [
            RegionRead.model_validate(item)
            for item in await self._repository.list_regions(country_id)
        ]
        await self._store(key, items, 86400)
        return items

    async def cities(self, region_id: uuid.UUID) -> list[CityRead]:
        """List cities within one region."""
        key = f"locations:region:{region_id}:cities:v1"
        cached = await self._cached(key)
        if cached is not None:
            return [CityRead.model_validate(item) for item in cached]
        items = [
            CityRead.model_validate(item)
            for item in await self._repository.list_cities(region_id)
        ]
        await self._store(key, items, 86400)
        return items

    async def search(self, query: str, limit: int) -> list[LocationSearchResult]:
        """Search fully qualified location labels."""
        return [
            LocationSearchResult.model_validate(item)
            for item in await self._repository.search(query, limit)
        ]

    async def _cached(self, key: str) -> Any | None:
        if self._cache is None:
            return None
        try:
            return await self._cache.get_json(key)
        except Exception:
            get_logger().warning("cache_read_failed", key=key)
            return None

    async def _store(self, key: str, items: list[Any], ttl: int) -> None:
        if self._cache is None:
            return
        try:
            await self._cache.set_json(
                key, [item.model_dump(mode="json") for item in items], ttl
            )
        except Exception:
            get_logger().warning("cache_write_failed", key=key)


class HotelService:
    """Own hotel search, details, and nearby business behavior."""

    def __init__(self, session: AsyncSession, cache: Cache | None = None) -> None:
        self._hotels = HotelRepository(session)
        self._attributes = AttributeRepository(session)
        self._cache = cache

    async def search(
        self,
        filters: HotelSearchFilters,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[HotelListItem], Pagination]:
        """Search hotels using an opaque ID cursor."""
        if (
            filters.min_rating is not None
            and filters.max_rating is not None
            and filters.min_rating > filters.max_rating
        ):
            raise ImportValidationError(
                "min_rating cannot be greater than max_rating"
            )
        cache_key = self._search_cache_key(filters, limit, cursor)
        if self._cache:
            try:
                cached = await self._cache.get_json(cache_key)
                if cached is not None:
                    return (
                        [HotelListItem.model_validate(item) for item in cached["items"]],
                        Pagination.model_validate(cached["pagination"]),
                    )
            except Exception:
                get_logger().warning("cache_read_failed", key=cache_key)
        cursor_values = decode_cursor(cursor)
        cursor_id = cursor_uuid(cursor_values, "id")
        if cursor and cursor_id is None:
            raise InvalidCursorError()
        required_value = {
            "name_asc": "name",
            "rating_desc": "rating",
            "review_count_desc": "reviews",
        }.get(filters.sort)
        if cursor and required_value and required_value not in cursor_values:
            raise InvalidCursorError()
        if filters.sort == "rating_desc":
            cursor_float(cursor_values, "rating")
        elif filters.sort == "review_count_desc":
            cursor_int(cursor_values, "reviews")
        rows = await self._hotels.search(filters, limit, cursor_values)
        has_more = len(rows) > limit
        selected = rows[:limit]
        items = [HotelListItem.model_validate(_row_dict(item)) for item in selected]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            cursor_payload: dict[str, Any] = {"id": last.id}
            if filters.sort == "name_asc":
                cursor_payload["name"] = last.name
            elif filters.sort == "rating_desc":
                cursor_payload["rating"] = (
                    last.source_rating if last.source_rating is not None else -1
                )
            elif filters.sort == "review_count_desc":
                cursor_payload["reviews"] = (
                    last.source_review_count if last.source_review_count is not None else -1
                )
            next_cursor = encode_cursor(cursor_payload)
        pagination = Pagination(next_cursor=next_cursor, has_more=has_more, limit=limit)
        if self._cache:
            try:
                await self._cache.set_json(
                    cache_key,
                    {
                        "items": [item.model_dump(mode="json") for item in items],
                        "pagination": pagination.model_dump(mode="json"),
                    },
                    120,
                )
            except Exception:
                get_logger().warning("cache_write_failed", key=cache_key)
        return items, pagination

    async def nearby(
        self, latitude: float, longitude: float, radius_km: float, limit: int
    ) -> list[HotelListItem]:
        """Find hotels around a point using PostGIS geography distance."""
        cache_key = (
            f"hotels:nearby:{latitude:.5f}:{longitude:.5f}:"
            f"{radius_km:.2f}:{limit}:v1"
        )
        if self._cache:
            try:
                cached = await self._cache.get_json(cache_key)
                if cached is not None:
                    return [HotelListItem.model_validate(item) for item in cached]
            except Exception:
                get_logger().warning("cache_read_failed", key=cache_key)
        rows = await self._hotels.nearby(latitude, longitude, int(radius_km * 1000), limit)
        items = [HotelListItem.model_validate(_row_dict(row)) for row in rows]
        if self._cache:
            try:
                await self._cache.set_json(
                    cache_key, [item.model_dump(mode="json") for item in items], 60
                )
            except Exception:
                get_logger().warning("cache_write_failed", key=cache_key)
        return items

    @staticmethod
    def _search_cache_key(
        filters: HotelSearchFilters, limit: int, cursor: str | None
    ) -> str:
        payload = {
            "filters": filters.model_dump(mode="json"),
            "limit": limit,
            "cursor": cursor,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return f"hotels:search:{digest}:v1"

    async def detail(self, hotel_id: uuid.UUID) -> HotelDetail:
        """Return an aggregated hotel detail projection."""
        cache_key = f"hotel:{hotel_id}:detail:v1"
        if self._cache:
            try:
                cached = await self._cache.get_json(cache_key)
                if cached is not None:
                    return HotelDetail.model_validate(cached)
            except Exception:
                get_logger().warning("cache_read_failed", key=cache_key)
        summary = await self._hotels.get_detail_row(hotel_id)
        if summary is None:
            raise HotelNotFoundError()
        images = [
            HotelImageRead.model_validate(item)
            for item in await self._hotels.images(hotel_id)
        ]
        sources = [
            SourceSummary.model_validate(_row_dict(item))
            for item in await self._hotels.source_rows(hotel_id)
        ]
        amenities = [
            AmenityRead.model_validate(item)
            for item in await self._hotels.amenities(hotel_id)
        ]
        detail = HotelDetail.model_validate(
            {**_row_dict(summary), "images": images, "amenities": amenities, "sources": sources}
        )
        if self._cache:
            try:
                await self._cache.set_json(cache_key, detail.model_dump(mode="json"), 900)
            except Exception:
                get_logger().warning("cache_write_failed", key=cache_key)
        return detail

    async def images(self, hotel_id: uuid.UUID) -> list[HotelImageRead]:
        """List hotel images after verifying the hotel exists."""
        if await self._hotels.get_model(hotel_id) is None:
            raise HotelNotFoundError()
        return [HotelImageRead.model_validate(item) for item in await self._hotels.images(hotel_id)]

    async def attributes(
        self, hotel_id: uuid.UUID, attribute_id: uuid.UUID | None = None
    ) -> list[HotelAttributeRead]:
        """List precomputed review-derived hotel attributes."""
        if await self._hotels.get_model(hotel_id) is None:
            raise HotelNotFoundError()
        cache_key = f"hotel:{hotel_id}:attributes:{attribute_id or 'all'}:v1"
        if self._cache:
            try:
                cached = await self._cache.get_json(cache_key)
                if cached is not None:
                    return [HotelAttributeRead.model_validate(item) for item in cached]
            except Exception:
                get_logger().warning("cache_read_failed", key=cache_key)
        rows = await self._attributes.hotel_scores(hotel_id, attribute_id)
        items = [HotelAttributeRead.model_validate(_row_dict(row)) for row in rows]
        if self._cache:
            try:
                await self._cache.set_json(
                    cache_key, [item.model_dump(mode="json") for item in items], 600
                )
            except Exception:
                get_logger().warning("cache_write_failed", key=cache_key)
        return items


class ReviewService:
    """Own review retrieval and filtering behavior."""

    def __init__(self, session: AsyncSession) -> None:
        self._reviews = ReviewRepository(session)
        self._hotels = HotelRepository(session)

    async def get(self, review_id: uuid.UUID) -> ReviewRead:
        """Return one review with normalized images."""
        row = await self._reviews.get(review_id)
        if row is None:
            raise ReviewNotFoundError()
        images = [
            ReviewImageRead.model_validate(item)
            for item in await self._reviews.images(review_id)
        ]
        return ReviewRead.model_validate({**_row_dict(row), "images": images})

    async def images(self, review_id: uuid.UUID) -> list[ReviewImageRead]:
        """Return review images after existence validation."""
        if await self._reviews.get(review_id) is None:
            raise ReviewNotFoundError()
        return [
            ReviewImageRead.model_validate(item)
            for item in await self._reviews.images(review_id)
        ]

    async def for_hotel(
        self,
        hotel_id: uuid.UUID,
        filters: ReviewFilters,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[ReviewRead], Pagination]:
        """Return a keyset page of filtered hotel reviews."""
        if (
            filters.date_from
            and filters.date_to
            and filters.date_from > filters.date_to
        ):
            raise ImportValidationError("date_from cannot be after date_to")
        if await self._hotels.get_model(hotel_id) is None:
            raise HotelNotFoundError()
        values = decode_cursor(cursor)
        if values.get("sort") not in {None, filters.sort}:
            raise InvalidCursorError()
        if filters.sort in {"newest", "oldest"}:
            cursor_value: date | float | None = cursor_date(values, "value")
        else:
            cursor_value = cursor_float(values, "value")
        cursor_id = cursor_uuid(values, "id")
        if cursor and (cursor_id is None or cursor_value is None):
            raise InvalidCursorError()
        rows = await self._reviews.list_for_hotel(
            hotel_id, filters, limit, cursor_value, cursor_id
        )
        has_more = len(rows) > limit
        selected = rows[:limit]
        items = [ReviewRead.model_validate({**_row_dict(row), "images": []}) for row in selected]
        last = items[-1] if items else None
        next_cursor = None
        if has_more and last:
            if filters.sort == "newest":
                sort_value: date | float = last.review_date or date.min
            elif filters.sort == "oldest":
                sort_value = last.review_date or date.max
            else:
                sort_value = last.normalized_rating_5
            next_cursor = encode_cursor(
                {"sort": filters.sort, "value": sort_value, "id": last.id}
            )
        return items, Pagination(next_cursor=next_cursor, has_more=has_more, limit=limit)


class AttributeService:
    """Expose the active attribute taxonomy."""

    def __init__(self, session: AsyncSession) -> None:
        self._attributes = AttributeRepository(session)

    async def list(self) -> list[AttributeRead]:
        """List active attributes grouped by display ordering."""
        return [
            AttributeRead.model_validate(row)
            for row in await self._attributes.list_attributes()
        ]

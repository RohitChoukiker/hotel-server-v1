"""Hotel search, nearby, detail, image, review, and attribute endpoints."""

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.responses import listing, success
from app.dependencies import CacheDep, SessionDep, rate_limit
from app.dto.common import ListResponse, SuccessResponse
from app.dto.hotels import (
    HotelAttributeRead,
    HotelDetail,
    HotelImageRead,
    HotelListItem,
    HotelSearchFilters,
)
from app.dto.reviews import ReviewFilters, ReviewRead
from app.enums import Sentiment
from app.services.catalog import HotelService, ReviewService

router = APIRouter(prefix="/hotels", tags=["hotels"])


@router.get(
    "",
    response_model=ListResponse[HotelListItem],
    dependencies=[Depends(rate_limit("search"))],
)
@router.get(
    "/search",
    response_model=ListResponse[HotelListItem],
    dependencies=[Depends(rate_limit("search"))],
)
async def search_hotels(
    request: Request,
    session: SessionDep,
    cache: CacheDep,
    country: uuid.UUID | None = None,
    region: uuid.UUID | None = None,
    city: uuid.UUID | None = None,
    hotel_type: str | None = None,
    min_rating: float | None = Query(default=None, ge=0, le=5),
    max_rating: float | None = Query(default=None, ge=0, le=5),
    attributes: list[str] = Query(default=[]),
    sort: Literal["rating_desc", "review_count_desc", "name_asc", "id_asc"] = "rating_desc",
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
) -> ListResponse[HotelListItem]:
    """Search hotels with indexed filters and cursor pagination."""
    filters = HotelSearchFilters(
        country_id=country,
        region_id=region,
        city_id=city,
        hotel_type=hotel_type,
        min_rating=min_rating,
        max_rating=max_rating,
        attribute_slugs=attributes,
        sort=sort,
    )
    items, pagination = await HotelService(session, cache).search(filters, limit, cursor)
    return listing(request, items, pagination)


@router.get(
    "/nearby",
    response_model=SuccessResponse[list[HotelListItem]],
    dependencies=[Depends(rate_limit("search"))],
)
async def nearby_hotels(
    request: Request,
    session: SessionDep,
    cache: CacheDep,
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    radius: float = Query(default=10, gt=0, le=200),
    limit: int = Query(default=20, ge=1, le=100),
) -> SuccessResponse[list[HotelListItem]]:
    """Find nearby hotels using PostGIS geography."""
    return success(
        request, await HotelService(session, cache).nearby(lat, lng, radius, limit)
    )


@router.get("/{hotel_id}", response_model=SuccessResponse[HotelDetail])
async def hotel_detail(
    hotel_id: uuid.UUID, request: Request, session: SessionDep, cache: CacheDep
) -> SuccessResponse[HotelDetail]:
    """Return canonical hotel details."""
    return success(request, await HotelService(session, cache).detail(hotel_id))


@router.get("/{hotel_id}/images", response_model=SuccessResponse[list[HotelImageRead]])
async def hotel_images(
    hotel_id: uuid.UUID, request: Request, session: SessionDep
) -> SuccessResponse[list[HotelImageRead]]:
    """Return normalized hotel images."""
    return success(request, await HotelService(session).images(hotel_id))


@router.get("/{hotel_id}/reviews", response_model=ListResponse[ReviewRead])
async def hotel_reviews(
    hotel_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    rating: float | None = Query(default=None, ge=0, le=5),
    trip_type: str | None = None,
    source: str | None = None,
    attribute: str | None = None,
    sentiment: Sentiment | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort: Literal["newest", "oldest", "rating_high", "rating_low"] = "newest",
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
) -> ListResponse[ReviewRead]:
    """Return filtered cursor-paged hotel reviews."""
    filters = ReviewFilters(
        rating=rating,
        trip_type=trip_type,
        source=source,
        attribute=attribute,
        sentiment=sentiment,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
    )
    items, pagination = await ReviewService(session).for_hotel(
        hotel_id, filters, limit, cursor
    )
    return listing(request, items, pagination)


@router.get(
    "/{hotel_id}/attributes", response_model=SuccessResponse[list[HotelAttributeRead]]
)
async def hotel_attributes(
    hotel_id: uuid.UUID, request: Request, session: SessionDep, cache: CacheDep
) -> SuccessResponse[list[HotelAttributeRead]]:
    """Return all precomputed hotel attribute scores."""
    return success(request, await HotelService(session, cache).attributes(hotel_id))


@router.get(
    "/{hotel_id}/attributes/{attribute_id}",
    response_model=SuccessResponse[HotelAttributeRead],
)
async def hotel_attribute(
    hotel_id: uuid.UUID,
    attribute_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    cache: CacheDep,
) -> SuccessResponse[HotelAttributeRead]:
    """Return one precomputed hotel attribute score."""
    rows = await HotelService(session, cache).attributes(hotel_id, attribute_id)
    if not rows:
        from app.exceptions import NotFoundError

        raise NotFoundError("Hotel attribute score not found")
    return success(request, rows[0])


@router.get(
    "/{hotel_id}/attributes/{attribute_id}/reviews",
    response_model=ListResponse[ReviewRead],
)
async def attribute_reviews(
    hotel_id: uuid.UUID,
    attribute_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    cache: CacheDep,
    sentiment: Sentiment | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
) -> ListResponse[ReviewRead]:
    """Return evidence reviews for a hotel attribute."""
    rows = await HotelService(session, cache).attributes(hotel_id, attribute_id)
    if not rows:
        from app.exceptions import NotFoundError

        raise NotFoundError("Hotel attribute score not found")
    filters = ReviewFilters(attribute=rows[0].slug, sentiment=sentiment)
    reviews, pagination = await ReviewService(session).for_hotel(
        hotel_id, filters, limit, cursor
    )
    return listing(request, reviews, pagination)

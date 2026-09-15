"""International geography HTTP endpoints."""

import uuid

from fastapi import APIRouter, Query, Request

from app.api.responses import success
from app.dependencies import CacheDep, SessionDep
from app.dto.common import SuccessResponse
from app.dto.locations import CityRead, CountryRead, LocationSearchResult, RegionRead
from app.services.catalog import LocationService

router = APIRouter(prefix="/locations", tags=["locations"])


@router.get("/countries", response_model=SuccessResponse[list[CountryRead]])
async def countries(
    request: Request, session: SessionDep, cache: CacheDep
) -> SuccessResponse[list[CountryRead]]:
    """List active countries."""
    return success(request, await LocationService(session, cache).countries())


@router.get(
    "/countries/{country_id}/regions", response_model=SuccessResponse[list[RegionRead]]
)
async def regions(
    country_id: uuid.UUID, request: Request, session: SessionDep, cache: CacheDep
) -> SuccessResponse[list[RegionRead]]:
    """List regions within a country."""
    return success(request, await LocationService(session, cache).regions(country_id))


@router.get("/regions/{region_id}/cities", response_model=SuccessResponse[list[CityRead]])
async def cities(
    region_id: uuid.UUID, request: Request, session: SessionDep, cache: CacheDep
) -> SuccessResponse[list[CityRead]]:
    """List cities within a region."""
    return success(request, await LocationService(session, cache).cities(region_id))


@router.get("/search", response_model=SuccessResponse[list[LocationSearchResult]])
async def search(
    request: Request,
    session: SessionDep,
    q: str = Query(min_length=2, max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
) -> SuccessResponse[list[LocationSearchResult]]:
    """Search fully-qualified locations."""
    return success(request, await LocationService(session).search(q, limit))

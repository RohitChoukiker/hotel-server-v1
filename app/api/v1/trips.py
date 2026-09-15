"""User-owned trip HTTP endpoints."""

import uuid

from fastapi import APIRouter, Request, status

from app.api.responses import success
from app.dependencies import CurrentUserDep, SessionDep
from app.dto.common import SuccessResponse
from app.dto.trips import TripPreferenceRead, TripPreferencesReplace, TripRead, TripWrite
from app.services.trips import TripService

router = APIRouter(prefix="/trips", tags=["trips"])


@router.post("", response_model=SuccessResponse[TripRead], status_code=status.HTTP_201_CREATED)
async def create_trip(
    payload: TripWrite,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> SuccessResponse[TripRead]:
    """Create a user-owned trip."""
    return success(request, await TripService(session).create(user.id, payload))


@router.get("", response_model=SuccessResponse[list[TripRead]])
async def list_trips(
    request: Request, user: CurrentUserDep, session: SessionDep
) -> SuccessResponse[list[TripRead]]:
    """List the current user's trips."""
    return success(request, await TripService(session).list(user.id))


@router.get("/{trip_id}", response_model=SuccessResponse[TripRead])
async def get_trip(
    trip_id: uuid.UUID,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> SuccessResponse[TripRead]:
    """Return one owned trip."""
    return success(request, await TripService(session).get(user.id, trip_id))


@router.put("/{trip_id}", response_model=SuccessResponse[TripRead])
async def update_trip(
    trip_id: uuid.UUID,
    payload: TripWrite,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> SuccessResponse[TripRead]:
    """Replace an owned trip's context."""
    return success(request, await TripService(session).update(user.id, trip_id, payload))


@router.delete("/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip(
    trip_id: uuid.UUID, user: CurrentUserDep, session: SessionDep
) -> None:
    """Soft-delete an owned trip."""
    await TripService(session).delete(user.id, trip_id)


@router.get(
    "/{trip_id}/preferences", response_model=SuccessResponse[list[TripPreferenceRead]]
)
async def trip_preferences(
    trip_id: uuid.UUID,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> SuccessResponse[list[TripPreferenceRead]]:
    """Return trip-specific overrides."""
    return success(request, await TripService(session).preferences(user.id, trip_id))


@router.put(
    "/{trip_id}/preferences", response_model=SuccessResponse[list[TripPreferenceRead]]
)
async def replace_trip_preferences(
    trip_id: uuid.UUID,
    payload: TripPreferencesReplace,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> SuccessResponse[list[TripPreferenceRead]]:
    """Atomically replace trip-specific preference overrides."""
    return success(
        request,
        await TripService(session).replace_preferences(user.id, trip_id, payload),
    )


"""Preference HTTP endpoints."""

import uuid

from fastapi import APIRouter, Request, status

from app.api.responses import success
from app.dependencies import CurrentUserDep, SessionDep
from app.dto.common import SuccessResponse
from app.dto.hotels import AttributeRead
from app.dto.preferences import PreferenceRead, PreferenceWrite, PreferencesReplace
from app.services.catalog import AttributeService
from app.services.preferences import PreferenceService

router = APIRouter(tags=["preferences"])


@router.get("/preferences/attributes", response_model=SuccessResponse[list[AttributeRead]])
async def attributes(request: Request, session: SessionDep) -> SuccessResponse[list[AttributeRead]]:
    """Return the active preference attribute taxonomy."""
    return success(request, await AttributeService(session).list())


@router.get("/users/me/preferences", response_model=SuccessResponse[list[PreferenceRead]])
async def list_preferences(
    request: Request, user: CurrentUserDep, session: SessionDep
) -> SuccessResponse[list[PreferenceRead]]:
    """Return effective permanent preferences."""
    return success(request, await PreferenceService(session).list_effective(user.id))


@router.put("/users/me/preferences", response_model=SuccessResponse[list[PreferenceRead]])
async def replace_preferences(
    payload: PreferencesReplace,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> SuccessResponse[list[PreferenceRead]]:
    """Atomically replace manual preferences."""
    return success(request, await PreferenceService(session).replace_manual(user.id, payload))


@router.patch(
    "/users/me/preferences/{attribute_id}",
    response_model=SuccessResponse[list[PreferenceRead]],
)
async def patch_preference(
    attribute_id: uuid.UUID,
    payload: PreferenceWrite,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> SuccessResponse[list[PreferenceRead]]:
    """Create or update one manual preference."""
    return success(
        request,
        await PreferenceService(session).upsert_manual(user.id, attribute_id, payload),
    )


@router.delete(
    "/users/me/preferences/{attribute_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_preference(
    attribute_id: uuid.UUID,
    user: CurrentUserDep,
    session: SessionDep,
) -> None:
    """Delete a manual preference override."""
    await PreferenceService(session).delete_manual(user.id, attribute_id)


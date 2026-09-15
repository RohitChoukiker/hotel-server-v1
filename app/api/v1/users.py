"""User profile HTTP endpoints."""

from fastapi import APIRouter, Request

from app.api.responses import success
from app.dependencies import CurrentUserDep, SessionDep
from app.dto.common import SuccessResponse
from app.dto.users import UserProfile, UserUpdate
from app.services.users import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=SuccessResponse[UserProfile])
async def get_profile(
    request: Request, user: CurrentUserDep, session: SessionDep
) -> SuccessResponse[UserProfile]:
    """Return the current user's profile."""
    return success(request, await UserService(session).get(user.id))


@router.put("/me", response_model=SuccessResponse[UserProfile])
async def update_profile(
    payload: UserUpdate,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> SuccessResponse[UserProfile]:
    """Update the current user's mutable profile."""
    return success(request, await UserService(session).update(user.id, payload))


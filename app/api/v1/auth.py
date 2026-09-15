"""Authentication HTTP endpoints."""

from fastapi import APIRouter, Depends, Request, status

from app.api.responses import success
from app.dependencies import CurrentUserDep, SessionDep, SettingsDep, rate_limit
from app.dto.auth import (
    AuthResult,
    AuthUser,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
)
from app.dto.common import SuccessResponse
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=SuccessResponse[AuthResult],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("auth"))],
)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> SuccessResponse[AuthResult]:
    """Register a new user and issue rotating tokens."""
    user, tokens = await AuthService(session, settings).register(
        payload,
        request.headers.get("user-agent"),
        request.client.host if request.client else None,
    )
    return success(request, AuthResult(user=user, tokens=tokens))


@router.post(
    "/login",
    response_model=SuccessResponse[AuthResult],
    dependencies=[Depends(rate_limit("auth"))],
)
async def login(
    payload: LoginRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> SuccessResponse[AuthResult]:
    """Authenticate a user."""
    user, tokens = await AuthService(session, settings).login(
        payload,
        request.headers.get("user-agent"),
        request.client.host if request.client else None,
    )
    return success(request, AuthResult(user=user, tokens=tokens))


@router.post(
    "/refresh",
    response_model=SuccessResponse[TokenPair],
    dependencies=[Depends(rate_limit("auth"))],
)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> SuccessResponse[TokenPair]:
    """Rotate a refresh token."""
    tokens = await AuthService(session, settings).refresh(
        payload.refresh_token,
        request.headers.get("user-agent"),
        request.client.host if request.client else None,
    )
    return success(request, tokens)


@router.post(
    "/logout",
    response_model=SuccessResponse[dict[str, bool]],
    dependencies=[Depends(rate_limit("auth"))],
)
async def logout(
    payload: LogoutRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> SuccessResponse[dict[str, bool]]:
    """Revoke a refresh token."""
    await AuthService(session, settings).logout(payload.refresh_token)
    return success(request, {"logged_out": True})


@router.get("/me", response_model=SuccessResponse[AuthUser])
async def me(user: CurrentUserDep, request: Request) -> SuccessResponse[AuthUser]:
    """Return the authenticated account."""
    return success(request, AuthUser.model_validate(user))

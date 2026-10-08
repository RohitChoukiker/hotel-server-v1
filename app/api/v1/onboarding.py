"""AI-assisted onboarding HTTP endpoints."""

import uuid

from fastapi import APIRouter, Request

from app.api.responses import success
from app.dependencies import AdaptiveProviderDep, CurrentUserDep, SessionDep
from app.dto.common import SuccessResponse
from app.dto.onboarding import (
    OnboardingAnswersRequest,
    OnboardingCompletionRead,
    OnboardingQuestionRead,
    OnboardingSessionRead,
    OnboardingSkipRead,
    OnboardingStartRequest,
    OnboardingStatusRead,
)
from app.exceptions import ImportValidationError
from app.services.onboarding import OnboardingService

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.post("/start", response_model=SuccessResponse[OnboardingSessionRead])
async def start(
    payload: OnboardingStartRequest,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    adaptive_provider: AdaptiveProviderDep,
) -> SuccessResponse[OnboardingSessionRead]:
    """Start or resume onboarding."""
    data = await OnboardingService(session, adaptive_provider).start(
        user.id, payload.question_set_version
    )
    return success(request, data)


@router.get(
    "/{session_id}/questions",
    response_model=SuccessResponse[list[OnboardingQuestionRead]],
)
async def questions(
    session_id: uuid.UUID,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> SuccessResponse[list[OnboardingQuestionRead]]:
    """Return the session's generated question history."""
    return success(request, await OnboardingService(session).questions(user.id, session_id))


@router.post("/skip", response_model=SuccessResponse[OnboardingSkipRead])
async def skip(
    request: Request, user: CurrentUserDep, session: SessionDep
) -> SuccessResponse[OnboardingSkipRead]:
    """Explicitly skip onboarding without claiming questionnaire completion."""
    return success(request, await OnboardingService(session).skip(user.id))


@router.post(
    "/{session_id}/answers",
    response_model=SuccessResponse[OnboardingSessionRead],
)
async def answers(
    session_id: uuid.UUID,
    payload: OnboardingAnswersRequest,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    adaptive_provider: AdaptiveProviderDep,
) -> SuccessResponse[OnboardingSessionRead]:
    """Submit or replace onboarding answers."""
    data = await OnboardingService(session, adaptive_provider).submit_answers(
        user.id, session_id, payload
    )
    return success(request, data)


@router.post("/{session_id}/complete", response_model=SuccessResponse[OnboardingCompletionRead])
async def complete(
    session_id: uuid.UUID,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> SuccessResponse[OnboardingCompletionRead]:
    """Persist the deterministic profile without calling an LLM."""
    service = OnboardingService(session)
    onboarding_session = await service.owned_session(user.id, session_id)
    if onboarding_session.mode != "ADAPTIVE":
        raise ImportValidationError("This legacy STATIC onboarding session must be restarted")
    data = await service.adaptive_complete(user.id, session_id)
    return success(request, OnboardingCompletionRead.model_validate(data))


@router.get("/status", response_model=SuccessResponse[OnboardingStatusRead])
async def status(
    request: Request, user: CurrentUserDep, session: SessionDep
) -> SuccessResponse[OnboardingStatusRead]:
    """Return current onboarding completion state."""
    data = await OnboardingService(session).status(user.id, user.onboarding_completed)
    return success(request, data)

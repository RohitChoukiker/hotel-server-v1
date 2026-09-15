"""AI-assisted onboarding HTTP endpoints."""

import uuid

from fastapi import APIRouter, Request

from app.api.responses import success
from app.dependencies import CurrentUserDep, InterpreterDep, SessionDep
from app.dto.common import SuccessResponse
from app.dto.onboarding import (
    OnboardingAnswersRequest,
    OnboardingQuestionRead,
    OnboardingSessionRead,
    OnboardingStartRequest,
    OnboardingStatusRead,
)
from app.orchestrators.onboarding import OnboardingOrchestrator
from app.services.onboarding import OnboardingService

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.post("/start", response_model=SuccessResponse[OnboardingSessionRead])
async def start(
    payload: OnboardingStartRequest,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> SuccessResponse[OnboardingSessionRead]:
    """Start or resume onboarding."""
    data = await OnboardingService(session).start(user.id, payload.question_set_version)
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
    """Return the session's 8 questions."""
    return success(request, await OnboardingService(session).questions(user.id, session_id))


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
) -> SuccessResponse[OnboardingSessionRead]:
    """Submit or replace onboarding answers."""
    data = await OnboardingService(session).submit_answers(user.id, session_id, payload)
    return success(request, data)


@router.post("/{session_id}/complete", response_model=SuccessResponse[dict[str, int]])
async def complete(
    session_id: uuid.UUID,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    interpreter: InterpreterDep,
) -> SuccessResponse[dict[str, int]]:
    """Interpret answers and build a structured preference profile."""
    count = await OnboardingOrchestrator(session, interpreter).complete(user.id, session_id)
    return success(request, {"preferences_created": count})


@router.get("/status", response_model=SuccessResponse[OnboardingStatusRead])
async def status(
    request: Request, user: CurrentUserDep, session: SessionDep
) -> SuccessResponse[OnboardingStatusRead]:
    """Return current onboarding completion state."""
    data = await OnboardingService(session).status(user.id, user.onboarding_completed)
    return success(request, data)


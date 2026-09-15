"""AI-assisted onboarding DTOs."""

import uuid
from typing import Any

from pydantic import Field

from app.dto.common import DTO


class OnboardingStartRequest(DTO):
    """Optional questionnaire version selector."""

    question_set_version: int | None = Field(default=None, ge=1)


class OnboardingSessionRead(DTO):
    """Onboarding session summary."""

    id: uuid.UUID
    question_set_id: uuid.UUID
    status: str
    answered_count: int
    question_count: int


class OnboardingQuestionRead(DTO):
    """One user-visible onboarding question."""

    id: uuid.UUID
    code: str
    prompt: str
    answer_type: str
    options: list[dict[str, Any]] | None
    position: int
    is_required: bool


class OnboardingAnswerWrite(DTO):
    """One structured or free-form onboarding answer."""

    question_id: uuid.UUID
    answer: dict[str, Any]


class OnboardingAnswersRequest(DTO):
    """Batch answer upsert."""

    answers: list[OnboardingAnswerWrite] = Field(min_length=1, max_length=8)


class OnboardingStatusRead(DTO):
    """Current user's onboarding status."""

    completed: bool
    active_session_id: uuid.UUID | None
    answered_count: int
    question_count: int


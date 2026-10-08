"""AI-assisted onboarding DTOs."""

import uuid
from typing import Any, Literal

from pydantic import Field, model_validator

from app.dto.common import DTO


class OnboardingStartRequest(DTO):
    """Legacy-compatible request; question-set selection is ignored."""

    question_set_version: int | None = Field(default=None, ge=1)


class OnboardingSessionRead(DTO):
    """Onboarding session summary."""

    id: uuid.UUID
    question_set_id: uuid.UUID | None = None
    status: str
    answered_count: int
    question_count: int
    mode: str = "ADAPTIVE"
    generation_state: str = "IDLE"
    completion_reason: str | None = None
    current_question: "OnboardingQuestionRead | None" = None


class OnboardingQuestionRead(DTO):
    """One user-visible onboarding question."""

    id: uuid.UUID
    code: str
    prompt: str
    answer_type: str
    options: list[dict[str, Any]] | None
    position: int
    is_required: bool
    helper_text: str | None = None
    question_kind: str = "standard"
    semantic_dimensions: list[str] = Field(default_factory=list)
    scale_min: float | None = None
    scale_max: float | None = None
    scale_labels: list[str] | None = None
    answer: dict[str, Any] | None = None


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
    skipped: bool = False
    active_session_id: uuid.UUID | None
    answered_count: int
    question_count: int
    mode: str = "ADAPTIVE"
    generation_state: str = "IDLE"
    completion_reason: str | None = None


class OnboardingSkipRead(DTO):
    """Result of an explicit user onboarding skip."""

    skipped: bool
    completed: bool
    session_id: uuid.UUID | None = None
    status: str


class OnboardingCompletionRead(DTO):
    """Completion result preserving the legacy preference count."""

    preferences_created: int
    profile: dict[str, Any] = Field(default_factory=dict)
    unresolved_attributes: list[str] = Field(default_factory=list)


SUPPORTED_ADAPTIVE_ANSWER_TYPES = {"single_choice", "multi_choice", "scale", "text"}

CANONICAL_ADAPTIVE_QUESTION_KEYS = (
    "adaptive_location",
    "travel_group",
    "price_value",
    "travel_pace",
    "food_preference",
    "destination_branch",
    "beach_activity",
    "beach_proximity",
    "mountain_outdoors",
    "mountain_scenery",
    "mountain_recovery",
    "city_focus",
    "city_location",
    "rural_isolation",
    "rural_stay_style",
    "destination_detail",
    "couple_style",
    "family_needs",
    "friends_vibe",
)

CanonicalAdaptiveQuestionKey = Literal[
    "adaptive_location",
    "travel_group",
    "price_value",
    "travel_pace",
    "food_preference",
    "destination_branch",
    "beach_activity",
    "beach_proximity",
    "mountain_outdoors",
    "mountain_scenery",
    "mountain_recovery",
    "city_focus",
    "city_location",
    "rural_isolation",
    "rural_stay_style",
    "destination_detail",
    "couple_style",
    "family_needs",
    "friends_vibe",
]


class AdaptiveQuestionOption(DTO):
    """One bounded option exposed by a generated question."""

    value: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    label: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=240)


class AdaptiveQuestion(DTO):
    """Strict provider output for one next question."""

    prompt: str = Field(min_length=1, max_length=500)
    helper_text: str | None = Field(default=None, max_length=500)
    answer_type: Literal["single_choice", "multi_choice", "scale", "text"]
    options: list[AdaptiveQuestionOption] | None = None
    scale_min: float | None = None
    scale_max: float | None = None
    scale_labels: list[str] | None = Field(default=None, max_length=5)
    required: bool = True
    question_kind: Literal["standard", "clarification"] = "standard"
    question_key: CanonicalAdaptiveQuestionKey
    semantic_dimensions: list[str] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_controls(self) -> "AdaptiveQuestion":
        if self.answer_type in {"single_choice", "multi_choice"}:
            minimum = 2
            maximum = 5 if self.answer_type == "single_choice" else 8
            if self.options is None or not minimum <= len(self.options) <= maximum:
                raise ValueError(f"{self.answer_type} requires {minimum}-{maximum} options")
            if len({item.value for item in self.options}) != len(self.options):
                raise ValueError("Question option values must be unique")
        elif self.answer_type == "scale":
            if self.options is not None or self.scale_min is None or self.scale_max is None:
                raise ValueError("Scale questions require bounds and no options")
            if self.scale_min >= self.scale_max:
                raise ValueError("Scale minimum must be below maximum")
        elif self.options is not None or self.scale_min is not None or self.scale_max is not None:
            raise ValueError("Text questions cannot define choice or scale controls")
        return self


class AdaptiveInferredSignal(DTO):
    """One high-confidence routing signal inferred by Claude."""

    routing_key: Literal[
        "destination_branch",
        "destination_detail",
        "couple_style",
        "family_needs",
        "friends_vibe",
    ]
    value: str = Field(min_length=1, max_length=120)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_question_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)


class NextQuestionDecision(DTO):
    """Strict provider output for the next-question decision."""

    questionnaire_complete: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str = Field(max_length=300)
    completion_reason: str | None = Field(default=None, max_length=80)
    inferred_signals: list[AdaptiveInferredSignal] = Field(default_factory=list, max_length=8)
    question: AdaptiveQuestion | None = None

    @model_validator(mode="after")
    def require_question_state(self) -> "NextQuestionDecision":
        if self.questionnaire_complete and self.question is not None:
            raise ValueError("Completed questionnaires cannot include a question")
        if not self.questionnaire_complete and self.question is None:
            raise ValueError("Incomplete questionnaires require one question")
        return self


class AdaptiveProfileContext(DTO):
    """Travel context inferred by the provider without hotel ranking."""

    destination_raw: str | None = None
    destination_normalized_text: str | None = None
    destination_country_id: uuid.UUID | None = None
    destination_region_id: uuid.UUID | None = None
    destination_city_id: uuid.UUID | None = None
    destination_display_name: str | None = None
    destination_resolution_status: Literal["resolved", "unresolved"] | None = None
    destination_resolution_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    destination_type: str | None = None
    group_type: str | None = None
    travel_pace: str | None = None
    budget_style: str | None = None


class AdaptiveProfilePreference(DTO):
    """One semantic preference to resolve against the live taxonomy."""

    attribute_slug: str = Field(min_length=1, max_length=160)
    importance_weight: float = Field(ge=0.0, le=1.0)
    preferred_value: str | None = Field(default=None, max_length=200)
    is_mandatory: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_question_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)


class AdaptivePreferenceProfile(DTO):
    """Legacy serialized profile shape retained for compatibility."""

    profile_summary: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0.0, le=1.0)
    context: AdaptiveProfileContext
    # Keep this field required for clients that still validate the legacy shape.
    preferences: list[AdaptiveProfilePreference] = Field(..., max_length=50)


OnboardingSessionRead.model_rebuild()

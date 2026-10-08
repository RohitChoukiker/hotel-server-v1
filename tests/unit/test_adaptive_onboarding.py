"""Unit coverage for adaptive provider contracts and offline behavior."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from app.config import LLMConfig
from app.dto.onboarding import (
    AdaptiveInferredSignal,
    AdaptivePreferenceProfile,
    AdaptiveProfilePreference,
    AdaptiveQuestion,
    NextQuestionDecision,
)
from app.enums import PreferenceSource
from app.exceptions import DependencyUnavailableError, ImportValidationError
from app.integrations.llm.adaptive import (
    AdaptiveProviderError,
    ClaudeUnavailableAdaptiveOnboardingProvider,
    DuplicateAdaptiveQuestionError,
)
from app.integrations.llm.anthropic import (
    AnthropicAdaptiveOnboardingProvider,
    _adaptive_question_tool_schema,
    _to_anthropic_strict_schema,
)
from app.models import OnboardingSession
from app.services.adaptive_preferences import build_deterministic_adaptive_profile
from app.services.onboarding import OnboardingService


def _context() -> dict[str, object]:
    return {
        "answered_count": 0,
        "questions": [],
        "answers": [],
        "allowed_attributes": [{"slug": "near-nature", "name": "Near nature"}],
    }


def _valid_question_input() -> dict[str, object]:
    return {
        "prompt": "Who are you travelling with?",
        "answer_type": "single_choice",
        "options": [
            {"value": "couple", "label": "Couple"},
            {"value": "solo", "label": "Solo"},
        ],
        "question_key": "travel_group",
        "semantic_dimensions": ["travel_group"],
        "scale_min": None,
        "scale_max": None,
        "scale_labels": None,
    }


def test_adaptive_question_contract_rejects_unbounded_choice_output() -> None:
    with pytest.raises(ValidationError):
        AdaptiveQuestion(
            prompt="Pick one",
            answer_type="single_choice",
            options=[{"value": "only", "label": "Only option"}],
            semantic_dimensions=["location"],
        )


def test_adaptive_question_requires_a_canonical_question_key() -> None:
    base = {
        "prompt": "Pick one",
        "answer_type": "single_choice",
        "options": [
            {"value": "quiet", "label": "Quiet"},
            {"value": "views", "label": "Views"},
        ],
        "semantic_dimensions": ["location"],
    }
    with pytest.raises(ValidationError):
        AdaptiveQuestion(**base)
    with pytest.raises(ValidationError):
        AdaptiveQuestion(**base, question_key="arbitrary_routing_key")


def test_inferred_signal_schema_remains_routing_only() -> None:
    with pytest.raises(ValidationError):
        AdaptiveInferredSignal(
            routing_key="price_value",
            value="balanced_value",
            confidence=0.95,
        )


def test_first_question_is_deterministic_location_text_input() -> None:
    question = OnboardingService._location_question()

    assert question.question_key == "adaptive_location"
    assert question.prompt == "Where are you travelling to?"
    assert question.answer_type == "text"
    assert question.options is None
    assert question.semantic_dimensions == ["location"]


def test_non_claude_adaptive_fallback_never_fabricates_a_question() -> None:
    provider = ClaudeUnavailableAdaptiveOnboardingProvider("claude-test", "test")
    with pytest.raises(AdaptiveProviderError):
        asyncio.run(provider.generate_next_question(_context()))


def test_anthropic_provider_requires_the_canonical_api_key() -> None:
    config = LLMConfig(provider="anthropic")
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    try:
        with pytest.raises(ValueError, match="API key"):
            AnthropicAdaptiveOnboardingProvider(config, client)
    finally:
        asyncio.run(client.aclose())


def test_adaptive_profile_schema_requires_exact_profile_shape() -> None:
    schema = AdaptivePreferenceProfile.model_json_schema()

    assert schema["required"] == [
        "profile_summary",
        "confidence",
        "context",
        "preferences",
    ]
    preferences_schema = schema["properties"]["preferences"]
    assert preferences_schema["type"] == "array"
    assert preferences_schema["maxItems"] == 50
    assert preferences_schema["items"] == {
        "$ref": "#/$defs/AdaptiveProfilePreference",
    }
    assert schema["$defs"]["AdaptiveProfilePreference"] == (
        AdaptiveProfilePreference.model_json_schema()
    )


def test_anthropic_schema_adapter_preserves_dtos_and_strict_structure() -> None:
    schemas = [
        AdaptivePreferenceProfile.model_json_schema(),
        NextQuestionDecision.model_json_schema(),
    ]
    unsupported = {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "minProperties",
        "maxProperties",
    }

    def walk(node: object) -> list[dict[str, object]]:
        if isinstance(node, dict):
            children = [child for key, child in node.items() if key != "$ref"]
            nested = [item for child in children for item in walk(child)]
            return [node, *nested]
        if isinstance(node, list):
            return [item for child in node for item in walk(child)]
        return []

    for raw_schema in schemas:
        raw_copy = json.loads(json.dumps(raw_schema))
        transformed = _to_anthropic_strict_schema(raw_schema)

        assert raw_schema == raw_copy
        assert all(keyword not in node for node in walk(transformed) for keyword in unsupported)
        assert all(
            node.get("additionalProperties") is False
            for node in walk(transformed)
            if node.get("type") == "object"
        )
        assert all("minItems" not in node for node in walk(transformed))
        assert transformed.get("$defs", {}).keys() == raw_schema.get("$defs", {}).keys()
        assert [node.get("$ref") for node in walk(transformed)] == [
            node.get("$ref") for node in walk(raw_schema)
        ]

    profile_schema = _to_anthropic_strict_schema(AdaptivePreferenceProfile.model_json_schema())
    assert profile_schema["required"] == [
        "profile_summary",
        "confidence",
        "context",
        "preferences",
    ]


def test_anthropic_question_tool_schema_requires_nullable_question_key() -> None:
    schema = _adaptive_question_tool_schema()

    assert schema["required"] == [
        "questionnaire_complete",
        "confidence",
        "reasoning_summary",
        "question",
    ]
    assert schema["properties"]["question"] == {
        "anyOf": [
            {"$ref": "#/$defs/AdaptiveQuestion"},
            {"type": "null"},
        ]
    }


def test_anthropic_provider_schema_is_compact_and_requires_nullable_question() -> None:
    schema = _adaptive_question_tool_schema()
    question = schema["properties"]["question"]
    definition_name = question["anyOf"][0]["$ref"].rsplit("/", 1)[-1]
    definition = schema["$defs"][definition_name]
    options = definition["properties"]["options"]

    assert schema["required"][-1] == "question"
    assert question["anyOf"][1] == {"type": "null"}
    assert "default" not in question
    assert options["anyOf"][0] == {
        "items": {"$ref": "#/$defs/AdaptiveQuestionOption"},
        "type": "array",
    }
    assert "minItems" not in options["anyOf"][0]
    assert "maxItems" not in options["anyOf"][0]
    assert definition["additionalProperties"] is False


def test_adaptive_question_accepts_a_valid_four_option_single_choice() -> None:
    question = AdaptiveQuestion(
        **{
            **_valid_question_input(),
            "options": [
                {"value": "solo", "label": "Solo"},
                {"value": "couple", "label": "Couple"},
                {"value": "family_with_kids", "label": "Family with kids"},
                {"value": "friends", "label": "Friends"},
            ],
        }
    )

    assert len(question.options or []) == 4


@pytest.mark.parametrize("count", [0, 1, 6])
def test_adaptive_question_rejects_invalid_single_choice_option_counts(count: int) -> None:
    options = [{"value": f"option_{index}", "label": f"Option {index}"} for index in range(count)]
    with pytest.raises(ValidationError, match="single_choice requires 2-5 options"):
        AdaptiveQuestion(**{**_valid_question_input(), "options": options})


def test_adaptive_question_scale_and_text_control_rules_remain_enforced() -> None:
    scale = AdaptiveQuestion(
        prompt="How important is this?",
        answer_type="scale",
        scale_min=1,
        scale_max=5,
        question_key="travel_pace",
        semantic_dimensions=["activities"],
    )
    text = AdaptiveQuestion(
        prompt="Where are you travelling to?",
        answer_type="text",
        question_key="adaptive_location",
        semantic_dimensions=["location"],
    )

    assert scale.options is None
    assert text.options is None
    with pytest.raises(ValidationError, match="Scale minimum must be below maximum"):
        AdaptiveQuestion(
            prompt="How important is this?",
            answer_type="scale",
            scale_min=5,
            scale_max=1,
            question_key="travel_pace",
            semantic_dimensions=["activities"],
        )
    with pytest.raises(ValidationError, match="Scale questions require bounds and no options"):
        AdaptiveQuestion(
            prompt="How important is this?",
            answer_type="scale",
            options=_valid_question_input()["options"],
            scale_min=1,
            scale_max=5,
            question_key="travel_pace",
            semantic_dimensions=["activities"],
        )
    with pytest.raises(
        ValidationError, match="Text questions cannot define choice or scale controls"
    ):
        AdaptiveQuestion(
            prompt="Where are you travelling to?",
            answer_type="text",
            scale_min=1,
            question_key="adaptive_location",
            semantic_dimensions=["location"],
        )


def test_adaptive_question_multi_choice_bounds_remain_enforced() -> None:
    options = [{"value": f"option_{index}", "label": f"Option {index}"} for index in range(8)]
    question = AdaptiveQuestion(
        prompt="What would you like to include?",
        answer_type="multi_choice",
        options=options,
        question_key="friends_vibe",
        semantic_dimensions=["social"],
    )

    assert len(question.options or []) == 8
    with pytest.raises(ValidationError, match="multi_choice requires 2-8 options"):
        AdaptiveQuestion(
            prompt="What would you like to include?",
            answer_type="multi_choice",
            options=[*options, {"value": "option_8", "label": "Option 8"}],
            question_key="friends_vibe",
            semantic_dimensions=["social"],
        )


def test_anthropic_400_schema_diagnostic_is_bounded_and_redacted() -> None:
    secret = "secret-anthropic-key"
    user_answer = "user-answer-must-not-leak"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "type": "invalid_request_error",
                    "message": (
                        "Schema compilation rejected maxLength; "
                        f"answer={user_answer}; api_key={secret}"
                    ),
                }
            },
        )

    async def exercise() -> None:
        config = LLMConfig(
            provider="anthropic",
            api_key=secret,
            model="claude-schema-error-test",
            max_retries=0,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AnthropicAdaptiveOnboardingProvider(config, client)
            with pytest.raises(AdaptiveProviderError) as raised:
                await provider.generate_next_question(_context())
            assert str(raised.value) == (
                "Claude schema request rejected: "
                "invalid_request_error: rejected schema constructs: maxLength"
            )

    with capture_logs() as logs:
        asyncio.run(exercise())

    serialized_logs = json.dumps(logs)
    assert secret not in serialized_logs
    assert user_answer not in serialized_logs


def test_deterministic_profile_maps_canonical_answers_without_fabrication() -> None:
    location = {
        "raw_input": "Aizawl",
        "display_name": "Aizawl",
        "normalized_location_text": "aizawl",
        "resolution_status": "resolved",
    }
    questions = [
        SimpleNamespace(
            id=f"q-{index}",
            position=index,
            generation_metadata={"question_key": key},
            semantic_dimensions=[],
            prompt=key,
        )
        for index, key in enumerate(
            [
                "adaptive_location",
                "travel_group",
                "price_value",
                "travel_pace",
                "food_preference",
                "destination_detail",
                "couple_style",
            ],
            start=1,
        )
    ]
    values = [
        "Aizawl",
        "couple",
        "balanced_value",
        "mixed_pace",
        "mix",
        "peaceful_isolation",
        "decompression",
    ]
    rows = [
        (question, SimpleNamespace(answer={"value": value}))
        for question, value in zip(questions, values, strict=False)
    ]
    rows.append((questions[-1], SimpleNamespace(answer={"value": "romance"})))

    first = build_deterministic_adaptive_profile(rows, location)
    second = build_deterministic_adaptive_profile(rows, location)

    assert first == second
    assert first.context["destination_display_name"] == "Aizawl"
    assert first.context["group_type"] == "couple"
    assert first.context["budget_style"] == "balanced_value"
    assert "quietness" in {item["attribute_slug"] for item in first.preferences}
    assert "price" in first.unresolved_attributes
    assert "Aizawl" in first.profile_summary
    assert "decompression" in first.profile_summary


def test_deterministic_profile_deduplicates_answers_and_ignores_unsupported_metadata() -> None:
    question = SimpleNamespace(
        id="q-1",
        position=1,
        generation_metadata={"question_key": "unknown_key"},
        semantic_dimensions=["unsupported_attribute"],
        prompt="Unsupported",
    )
    supported = SimpleNamespace(
        id="q-2",
        position=2,
        generation_metadata={"question_key": "travel_pace"},
        semantic_dimensions=[],
        prompt="Pace",
    )
    rows = [
        (question, SimpleNamespace(answer={"value": "invented"})),
        (supported, SimpleNamespace(answer={"value": "active_packed"})),
        (supported, SimpleNamespace(answer={"value": "active_packed"})),
    ]

    profile = build_deterministic_adaptive_profile(rows, {})
    slugs = [item["attribute_slug"] for item in profile.preferences]

    assert "unsupported_attribute" not in slugs
    assert profile.signals["answer_values"]["travel_pace"] == ["active_packed"]
    assert profile.signals["canonical_scores"]["activities"] == 1.0


def test_adaptive_complete_is_deterministic_and_does_not_call_provider() -> None:
    session = SimpleNamespace(
        id="session-id",
        mode="ADAPTIVE",
        status="READY_TO_COMPLETE",
        generation_state="IDLE",
        profile_payload={"location_context": {"raw_input": "Aizawl"}},
        completion_reason=None,
    )

    class ProviderWithoutCompletionMethod:
        provider_name = "anthropic"
        model = "claude-question-only"

    question = SimpleNamespace(
        id="q-1",
        position=1,
        generation_metadata={"question_key": "travel_pace"},
        semantic_dimensions=[],
        prompt="Pace",
    )
    rows = [(question, SimpleNamespace(answer={"value": "active_packed"}))]

    class Attributes:
        def __init__(self, session: object) -> None:
            del session

        async def resolve_onboarding_attribute(self, slug: str) -> dict[str, str] | None:
            return {"id": f"attribute-{slug}", "slug": slug, "name": slug}

    persisted: list[tuple[object, object, float, object, PreferenceSource]] = []

    class Preferences:
        def __init__(self, session: object) -> None:
            del session

        async def upsert(
            self,
            user_id: object,
            attribute_id: object,
            importance_weight: float,
            preferred_value: object,
            source: PreferenceSource,
        ) -> None:
            persisted.append((user_id, attribute_id, importance_weight, preferred_value, source))

    user = SimpleNamespace(onboarding_completed=False)
    users = SimpleNamespace(get_by_id=AsyncMock(return_value=user))

    service = OnboardingService.__new__(OnboardingService)
    service._adaptive_provider = ProviderWithoutCompletionMethod()
    service._session = SimpleNamespace(commit=AsyncMock())
    service._onboarding = SimpleNamespace(
        counts=AsyncMock(return_value=(7, 7)),
        adaptive_answer_rows=AsyncMock(return_value=rows),
        get_session=AsyncMock(return_value=session),
    )
    service._owned = AsyncMock(return_value=session)
    service._completion_readiness = lambda *args, **kwargs: {"ready": True}

    async def exercise() -> None:
        with (
            patch("app.services.onboarding.AttributeRepository", Attributes),
            patch("app.services.onboarding.PreferenceRepository", Preferences),
            patch("app.services.onboarding.UserRepository", lambda _: users),
        ):
            first = await service.adaptive_complete("user-id", "session-id")
            second = await service.adaptive_complete("user-id", "session-id")
        assert first == second

    asyncio.run(exercise())
    assert session.status == "COMPLETED"
    assert session.generation_state == "IDLE"
    assert user.onboarding_completed is True
    assert persisted
    assert all(item[-1] is PreferenceSource.ONBOARDING_DETERMINISTIC for item in persisted)


def test_concurrent_profile_generation_is_rejected() -> None:
    session = SimpleNamespace(
        id="session-id",
        mode="ADAPTIVE",
        status="READY_TO_COMPLETE",
        generation_state="PROFILE_GENERATING",
        profile_payload={},
    )
    provider = SimpleNamespace(provider_name="anthropic", model="claude-profile-concurrent")
    service = OnboardingService.__new__(OnboardingService)
    service._adaptive_provider = provider
    service._onboarding = SimpleNamespace()
    service._owned = AsyncMock(return_value=session)

    async def exercise() -> None:
        with pytest.raises(DependencyUnavailableError, match="already in progress"):
            await service.adaptive_complete("user-id", "session-id")

    asyncio.run(exercise())


def test_legacy_static_sessions_cannot_enter_adaptive_flow() -> None:
    session = OnboardingSession(mode="STATIC")
    with pytest.raises(ImportValidationError):
        OnboardingService._require_adaptive_session(session)


def test_anthropic_provider_forces_tool_and_validates_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "adaptive_onboarding_question",
                        "input": {
                            "questionnaire_complete": False,
                            "confidence": 0.7,
                            "reasoning_summary": "Need destination context.",
                            "question": {
                                "prompt": "Where do you want to go?",
                                "answer_type": "single_choice",
                                "options": [
                                    {"value": "beach", "label": "Beach"},
                                    {"value": "city", "label": "City"},
                                ],
                                "question_key": "destination_branch",
                                "semantic_dimensions": ["location"],
                            },
                        },
                    }
                ]
            },
        )

    async def exercise() -> None:
        config = LLMConfig(
            provider="anthropic",
            api_key="test-key",
            model="claude-configured-test-model",
            base_url="https://api.anthropic.com",
            timeout_s=17,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AnthropicAdaptiveOnboardingProvider(config, client)
            context = _context()
            context.update(
                {
                    "destination": {"raw_input": "Goa"},
                    "questions": [
                        {
                            "position": 1,
                            "prompt": "Where are you travelling to?",
                            "answer": {"value": "Goa"},
                        },
                        {
                            "position": 2,
                            "prompt": "Who are you travelling with?",
                            "answer": {"selected": ["couple"]},
                        },
                    ],
                    "answers": [
                        {"question_id": "q1", "answer": {"value": "Goa"}},
                        {"question_id": "q2", "answer": {"selected": ["couple"]}},
                    ],
                    "previously_inferred_preferences": [{"attribute_slug": "privacy"}],
                }
            )
            result = await provider.generate_next_question(context)
            assert result.question is not None

    asyncio.run(exercise())
    assert requests[0].headers["x-api-key"] == "test-key"
    assert str(requests[0].url) == "https://api.anthropic.com/v1/messages"
    body = json.loads(requests[0].read())
    assert body["tools"] == [
        {
            "name": "adaptive_onboarding_question",
            "description": "Return only the structured onboarding object.",
            "strict": True,
            "input_schema": _adaptive_question_tool_schema(),
        }
    ]
    assert body["tool_choice"] == {
        "type": "tool",
        "name": "adaptive_onboarding_question",
    }
    assert body["model"] == "claude-configured-test-model"
    serialized_body = json.dumps(body)
    assert "Goa" in serialized_body
    assert "Who are you travelling with?" in serialized_body
    assert "privacy" in serialized_body
    prompt = AnthropicAdaptiveOnboardingProvider._system_prompt("next question")
    assert "exactly one destination branch" in prompt
    assert "universal core" in prompt
    assert "canonical question_key is already answered" in prompt
    assert "never exceed 8" in prompt
    assert "maximum 250 characters" in prompt
    assert "maximum 60 characters" in prompt
    assert "Always emit the `question` key" in prompt
    assert (
        "questionnaire_complete is false, `question` must contain exactly one valid question"
        in prompt
    )
    assert "questionnaire_complete is true, `question` must be null" in prompt
    assert "For single_choice, emit 2-5 options and null scale controls" in prompt
    assert "Do not provide detailed reasoning" in prompt


def test_incomplete_decision_without_question_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Incomplete questionnaires require one question"):
        NextQuestionDecision(
            questionnaire_complete=False,
            confidence=0.8,
            reasoning_summary="Need travel group.",
        )


def test_incomplete_decision_with_question_and_complete_null_question_are_valid() -> None:
    incomplete = NextQuestionDecision(
        questionnaire_complete=False,
        confidence=0.8,
        reasoning_summary="Need travel group.",
        question=_valid_question_input(),
    )
    complete = NextQuestionDecision(
        questionnaire_complete=True,
        confidence=0.9,
        reasoning_summary="Canonical signals are complete.",
        question=None,
    )

    assert incomplete.question is not None
    assert complete.question is None


def test_anthropic_rejects_live_style_incomplete_payload_without_question() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "adaptive_onboarding_question",
                        "input": {
                            "questionnaire_complete": False,
                            "confidence": 0.8,
                            "reasoning_summary": "Need travel group.",
                            "inferred_signals": [],
                        },
                    }
                ]
            },
        )

    async def exercise() -> None:
        config = LLMConfig(
            provider="anthropic",
            api_key="test-key",
            model="claude-live-payload-test",
            max_retries=0,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AnthropicAdaptiveOnboardingProvider(config, client)
            with pytest.raises(AdaptiveProviderError) as raised:
                await provider.generate_next_question(_context())
            assert isinstance(raised.value.__cause__, ValidationError)

    asyncio.run(exercise())
    body = json.loads(requests[0].read())
    assert "question" in body["tools"][0]["input_schema"]["required"]


def test_anthropic_rejects_live_single_choice_payload_with_one_option() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "adaptive_onboarding_question",
                        "input": {
                            "questionnaire_complete": False,
                            "confidence": 0.8,
                            "reasoning_summary": "Need travel group.",
                            "question": {
                                **_valid_question_input(),
                                "options": [{"value": "solo", "label": "Solo"}],
                            },
                        },
                    }
                ]
            },
        )

    async def exercise() -> None:
        config = LLMConfig(
            provider="anthropic",
            api_key="test-key",
            model="claude-live-options-test",
            max_retries=0,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AnthropicAdaptiveOnboardingProvider(config, client)
            with pytest.raises(AdaptiveProviderError) as raised:
                await provider.generate_next_question(_context())
            assert isinstance(raised.value.__cause__, ValidationError)

    asyncio.run(exercise())
    schema = json.loads(requests[0].read())["tools"][0]["input_schema"]
    definition = schema["$defs"]["AdaptiveQuestion"]
    array_schema = definition["properties"]["options"]["anyOf"][0]
    assert "minItems" not in array_schema
    assert "maxItems" not in array_schema


def test_anthropic_provider_has_no_final_profile_generation_method() -> None:
    assert not hasattr(AnthropicAdaptiveOnboardingProvider, "generate_profile")
    assert not hasattr(ClaudeUnavailableAdaptiveOnboardingProvider, "generate_profile")


def test_anthropic_question_confidence_remains_pydantic_validated() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "adaptive_onboarding_question",
                        "input": {
                            "questionnaire_complete": True,
                            "confidence": 1.1,
                            "reasoning_summary": "Invalid confidence.",
                        },
                    }
                ]
            },
        )

    async def exercise() -> None:
        config = LLMConfig(
            provider="anthropic",
            api_key="test-key",
            model="claude-confidence-validation-test",
            max_retries=0,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AnthropicAdaptiveOnboardingProvider(config, client)
            with pytest.raises(AdaptiveProviderError) as raised:
                await provider.generate_next_question(_context())
            assert isinstance(raised.value.__cause__, ValidationError)

    asyncio.run(exercise())


def test_anthropic_normalizes_only_verbose_internal_metadata() -> None:
    requests: list[httpx.Request] = []
    reasoning = "r" * 350
    completion = "c" * 100

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "adaptive_onboarding_question",
                        "input": {
                            "questionnaire_complete": False,
                            "confidence": 0.8,
                            "reasoning_summary": reasoning,
                            "completion_reason": completion,
                            "question": {
                                "prompt": "What matters most?",
                                "answer_type": "single_choice",
                                "options": [
                                    {"value": "quiet", "label": "Quiet"},
                                    {"value": "views", "label": "Views"},
                                ],
                                "question_key": "destination_detail",
                                "semantic_dimensions": ["location"],
                            },
                        },
                    }
                ]
            },
        )

    async def exercise() -> NextQuestionDecision:
        config = LLMConfig(
            provider="anthropic",
            api_key="test-key",
            model="claude-metadata-test",
            max_retries=0,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AnthropicAdaptiveOnboardingProvider(config, client)
            return await provider.generate_next_question(_context())

    with capture_logs() as logs:
        result = asyncio.run(exercise())

    assert len(requests) == 1
    assert result.reasoning_summary == reasoning[:300]
    assert result.completion_reason == completion[:80]
    normalized = [
        item for item in logs if item.get("event") == "adaptive_claude_metadata_normalized"
    ]
    assert {
        (item["field"], item["original_length"], item["final_length"]) for item in normalized
    } == {
        ("reasoning_summary", 350, 300),
        ("completion_reason", 100, 80),
    }


def test_anthropic_drops_unsupported_inferred_signal_and_preserves_decision() -> None:
    requests: list[httpx.Request] = []
    secret = "private-routing-value"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "adaptive_onboarding_question",
                        "input": {
                            "questionnaire_complete": False,
                            "confidence": 0.91,
                            "reasoning_summary": "Ask for the missing couple signal.",
                            "completion_reason": "Still collecting canonical coverage",
                            "inferred_signals": [
                                {
                                    "routing_key": "price_value",
                                    "value": "balanced_value",
                                    "confidence": 0.99,
                                },
                                {
                                    "routing_key": "destination_branch",
                                    "value": "city_town",
                                    "confidence": 0.97,
                                },
                                {
                                    "routing_key": "couple_style",
                                    "value": "decompression",
                                    "confidence": 0.96,
                                },
                                {
                                    "routing_key": secret,
                                    "value": "do not log me",
                                    "confidence": 0.99,
                                },
                            ],
                            "question": {
                                "prompt": "What matters most as a couple?",
                                "answer_type": "single_choice",
                                "options": [
                                    {"value": "romance", "label": "Romance"},
                                    {"value": "decompression", "label": "Decompression"},
                                ],
                                "question_key": "couple_style",
                                "semantic_dimensions": ["couple_style"],
                            },
                        },
                    }
                ]
            },
        )

    async def exercise() -> NextQuestionDecision:
        config = LLMConfig(
            provider="anthropic",
            api_key="test-key",
            model="claude-inferred-signal-test",
            max_retries=0,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AnthropicAdaptiveOnboardingProvider(config, client)
            return await provider.generate_next_question(_context())

    with capture_logs() as logs:
        result = asyncio.run(exercise())

    assert len(requests) == 1
    assert result.questionnaire_complete is False
    assert result.reasoning_summary == "Ask for the missing couple signal."
    assert result.completion_reason == "Still collecting canonical coverage"
    assert result.question is not None
    assert [signal.routing_key for signal in result.inferred_signals] == [
        "destination_branch",
        "couple_style",
    ]
    dropped = [
        item for item in logs if item.get("event") == "adaptive_claude_inferred_signal_dropped"
    ]
    assert len(dropped) == 2
    assert {item["routing_key"] for item in dropped} == {"price_value", "[unsupported]"}
    assert all(item["tool_name"] == "adaptive_onboarding_question" for item in dropped)
    assert all(item["attempt"] == 1 for item in dropped)
    assert all(item["model"] == "claude-inferred-signal-test" for item in dropped)
    assert secret not in json.dumps(logs)


def test_anthropic_retry_includes_sanitized_validation_feedback() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            options = [{"value": "only", "label": "Only option"}]
        else:
            options = [
                {"value": "quiet", "label": "Quiet"},
                {"value": "views", "label": "Views"},
            ]
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "adaptive_onboarding_question",
                        "input": {
                            "questionnaire_complete": False,
                            "confidence": 0.8,
                            "reasoning_summary": "Need one signal.",
                            "question": {
                                **_valid_question_input(),
                                "prompt": "What matters most?",
                                "options": options,
                                "question_key": "destination_detail",
                                "semantic_dimensions": ["location"],
                            },
                        },
                    }
                ]
            },
        )

    async def no_sleep(_: float) -> None:
        return None

    async def exercise() -> NextQuestionDecision:
        config = LLMConfig(
            provider="anthropic",
            api_key="test-key",
            model="claude-feedback-test",
            max_retries=1,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AnthropicAdaptiveOnboardingProvider(config, client)
            return await provider.generate_next_question(_context())

    original_sleep = asyncio.sleep
    asyncio.sleep = no_sleep
    try:
        result = asyncio.run(exercise())
    finally:
        asyncio.sleep = original_sleep

    assert result.question is not None
    assert len(requests) == 2
    retry_body = requests[1].read().decode()
    assert "Your previous structured output was invalid" in retry_body
    assert "question.options" in retry_body
    assert "question.answer_type: use `single_choice`" in retry_body
    assert "question.options: include 2-5 option objects" in retry_body
    assert "question.options: every option requires `value` and `label`" in retry_body
    assert "question: return the complete question object again" in retry_body
    assert "Only option" not in retry_body
    retry_schema = json.loads(retry_body)["tools"][0]["input_schema"]
    retry_question = retry_schema["$defs"]["AdaptiveQuestion"]
    assert "options" in retry_question["required"]
    assert retry_question["properties"]["options"] == {
        "type": "array",
        "items": {"$ref": "#/$defs/AdaptiveQuestionOption"},
    }


def test_anthropic_validation_logs_are_sanitized_and_retries_exhaust() -> None:
    requests: list[httpx.Request] = []
    secret = "super-secret-anthropic-key"
    raised_error: AdaptiveProviderError | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "adaptive_onboarding_question",
                        "input": {
                            "questionnaire_complete": False,
                            "confidence": 0.7,
                            "reasoning_summary": "Need more context.",
                            "question": {
                                "prompt": "user-answer-must-not-log",
                                "answer_type": "single_choice",
                                "options": [{"value": "only", "label": "Only option"}],
                                "semantic_dimensions": ["location"],
                                "api_key": secret,
                            },
                        },
                    }
                ]
            },
        )

    async def no_sleep(_: float) -> None:
        return None

    async def exercise() -> None:
        nonlocal raised_error
        config = LLMConfig(
            provider="anthropic",
            api_key=secret,
            model="claude-validation-test",
            max_retries=2,
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = AnthropicAdaptiveOnboardingProvider(config, client)
            with pytest.raises(AdaptiveProviderError) as raised:
                await provider.generate_next_question(_context())
            raised_error = raised.value
        assert str(raised.value) == ("Claude returned no valid adaptive_onboarding_question output")
        assert isinstance(raised.value.__cause__, ValidationError)

    original_sleep = asyncio.sleep
    asyncio.sleep = no_sleep
    try:
        with capture_logs() as logs:
            asyncio.run(exercise())
    finally:
        asyncio.sleep = original_sleep

    attempts = [item for item in logs if item.get("event") == "adaptive_claude_validation_failed"]
    assert len(requests) == 3
    assert len(attempts) == 3
    assert [item["attempt"] for item in attempts] == [1, 2, 3]
    assert all(item["tool_name"] == "adaptive_onboarding_question" for item in attempts)
    assert all(item["error_type"] == "ValidationError" for item in attempts)
    assert all("input" not in error for item in attempts for error in item["validation_errors"])

    exhausted = [
        item for item in logs if item.get("event") == "adaptive_claude_validation_exhausted"
    ]
    assert len(exhausted) == 1
    assert exhausted[0]["attempts"] == 3
    assert exhausted[0]["final_error_type"] == "ValidationError"
    assert exhausted[0]["validation_errors"]

    serialized_logs = json.dumps(logs)
    assert secret not in serialized_logs
    assert "user-answer-must-not-log" not in serialized_logs
    assert raised_error is not None
    assert DependencyUnavailableError(raised_error.public_message).public_message == (
        "Claude adaptive onboarding provider failed"
    )


def test_completion_decision_cannot_include_question() -> None:
    with pytest.raises(ValidationError):
        NextQuestionDecision(
            questionnaire_complete=True,
            confidence=0.9,
            reasoning_summary="Done",
            question=_valid_question_input(),
        )


def test_validate_question_rejects_answered_canonical_topic_with_new_wording() -> None:
    prior = SimpleNamespace(
        prompt="How do you balance price and quality?",
        generation_metadata={"question_key": "price_value"},
        semantic_dimensions=["price_value"],
    )
    proposed = AdaptiveQuestion(
        prompt="How important is price versus quality when choosing your hotel?",
        answer_type="single_choice",
        options=[
            {"value": "price", "label": "Keep the price low"},
            {"value": "quality", "label": "Pay more for quality"},
        ],
        question_key="price_value",
        semantic_dimensions=["budget_sensitivity", "value_expectation"],
    )
    answered_rows = [(prior, SimpleNamespace(answer={"selected": ["balanced_value"]}))]

    with pytest.raises(DuplicateAdaptiveQuestionError):
        OnboardingService._validate_question(proposed, [prior], answered_rows)


def test_validate_question_maps_legacy_semantic_price_dimensions() -> None:
    prior = SimpleNamespace(
        prompt="What kind of value matters to you?",
        generation_metadata={},
        semantic_dimensions=["price_value", "budget_sensitivity", "value_expectation"],
    )
    proposed = AdaptiveQuestion(
        prompt="Would you spend more for a better room?",
        answer_type="single_choice",
        options=[
            {"value": "yes", "label": "Yes"},
            {"value": "no", "label": "No"},
        ],
        question_key="price_value",
        semantic_dimensions=["price_value"],
    )
    answered_rows = [(prior, SimpleNamespace(answer={"selected": ["balanced_value"]}))]

    with pytest.raises(DuplicateAdaptiveQuestionError):
        OnboardingService._validate_question(proposed, [prior], answered_rows)


def test_aizawl_balanced_clarification_is_not_completion_ready() -> None:
    def question(
        key: str,
        *,
        kind: str = "standard",
        code: str | None = None,
        dimensions: list[str] | None = None,
    ) -> SimpleNamespace:
        metadata = {"question_key": key}
        if code is not None:
            metadata["code"] = code
        return SimpleNamespace(
            generation_metadata=metadata,
            question_kind=kind,
            semantic_dimensions=dimensions or [],
        )

    def answered(item: SimpleNamespace, answer: dict[str, object]) -> tuple[object, object]:
        return item, SimpleNamespace(answer=answer, interpreted_preferences=None)

    rows = [
        answered(
            question("adaptive_location", code="adaptive_location", dimensions=["location"]),
            {"value": "Aizawl, Mizoram"},
        ),
        answered(question("group_type"), {"selected": ["couple"]}),
        answered(question("price_value"), {"selected": ["balanced_value"]}),
        answered(question("travel_pace"), {"selected": ["mixed_pace"]}),
        answered(question("food_preference"), {"selected": ["mix"]}),
        answered(
            question("destination_type", kind="clarification"),
            {"selected": ["both_balanced"]},
        ),
    ]

    readiness = OnboardingService._completion_readiness(
        SimpleNamespace(profile_payload={}), rows=rows, answered_count=6
    )

    assert readiness["ready"] is False
    assert "destination_matching_signal" in readiness["blockers"]
    assert "couple_style" in readiness["blockers"]
    assert readiness["branch_state"]["destination_branch_selected"] is False


def test_aizawl_city_focus_and_couple_decompression_are_completion_ready() -> None:
    def question(
        key: str,
        *,
        code: str | None = None,
        dimensions: list[str] | None = None,
    ) -> SimpleNamespace:
        metadata = {"question_key": key}
        if code is not None:
            metadata["code"] = code
        return SimpleNamespace(
            generation_metadata=metadata,
            question_kind="standard",
            semantic_dimensions=dimensions or [],
        )

    def answered(item: SimpleNamespace, value: str) -> tuple[object, object]:
        return item, SimpleNamespace(answer={"selected": [value]}, interpreted_preferences=None)

    rows = [
        (
            question("adaptive_location", code="adaptive_location", dimensions=["location"]),
            SimpleNamespace(answer={"value": "Aizawl, Mizoram"}, interpreted_preferences=None),
        ),
        answered(question("travel_group"), "couple"),
        answered(question("price_value"), "balanced_value"),
        answered(question("travel_pace"), "mixed_pace"),
        answered(question("food_preference"), "mix"),
        answered(question("city_focus"), "peaceful_retreat"),
        answered(question("couple_style"), "decompression"),
    ]

    readiness = OnboardingService._completion_readiness(
        SimpleNamespace(profile_payload={}), rows=rows, answered_count=7
    )

    assert readiness["ready"] is True
    assert readiness["blockers"] == []
    assert readiness["branch_state"]["destination_branch"] == "city_town"
    assert readiness["branch_state"]["destination_matching_signal_captured"] is True
    assert readiness["branch_state"]["group_signal_captured"] is True


def test_legacy_routing_compatibility_is_conservative() -> None:
    def question(
        key: str | None,
        dimensions: list[str],
        *,
        code: str | None = None,
    ) -> SimpleNamespace:
        metadata = {}
        if key is not None:
            metadata["question_key"] = key
        if code is not None:
            metadata["code"] = code
        return SimpleNamespace(
            generation_metadata=metadata,
            question_kind="standard",
            semantic_dimensions=dimensions,
        )

    def answered(item: SimpleNamespace, value: str) -> tuple[object, object]:
        return item, SimpleNamespace(answer={"selected": [value]}, interpreted_preferences=None)

    rows = [
        (
            question("adaptive_location", ["location"], code="adaptive_location"),
            SimpleNamespace(answer={"value": "Aizawl, Mizoram"}, interpreted_preferences=None),
        ),
        answered(question("group_type", ["group"]), "couple"),
        answered(question("price_value", ["price"]), "balanced_value"),
        answered(question("travel_pace", ["activities"]), "mixed_pace"),
        answered(question("food_preference", ["food"]), "mix"),
        answered(question(None, ["urban_location"]), "peaceful_retreat"),
        answered(question(None, ["couple_style"]), "decompression"),
    ]

    readiness = OnboardingService._completion_readiness(
        SimpleNamespace(profile_payload={}), rows=rows, answered_count=7
    )
    assert readiness["ready"] is True
    assert readiness["branch_state"]["destination_branch"] == "city_town"

    ambiguous = list(rows)
    ambiguous[5] = answered(question(None, ["urban_location", "travel_intent"]), "peaceful_retreat")
    ambiguous_readiness = OnboardingService._completion_readiness(
        SimpleNamespace(profile_payload={}), rows=ambiguous, answered_count=7
    )
    assert ambiguous_readiness["ready"] is False
    assert "destination_branch" in ambiguous_readiness["blockers"]

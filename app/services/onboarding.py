"""Claude-powered adaptive onboarding workflows."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.logging import get_logger
from app.dto.onboarding import (
    CANONICAL_ADAPTIVE_QUESTION_KEYS,
    AdaptiveInferredSignal,
    AdaptiveQuestion,
    OnboardingAnswersRequest,
    OnboardingQuestionRead,
    OnboardingSessionRead,
    OnboardingSkipRead,
    OnboardingStatusRead,
)
from app.enums import PreferenceSource
from app.exceptions import (
    DependencyUnavailableError,
    ImportValidationError,
    OnboardingSessionNotFoundError,
    UserNotFoundError,
)
from app.integrations.llm.adaptive import (
    AdaptiveProviderError,
    DuplicateAdaptiveQuestionError,
)
from app.integrations.llm.base import AdaptiveOnboardingProvider
from app.models import OnboardingSession, OnboardingSessionQuestion
from app.repositories.catalog import AttributeRepository
from app.repositories.locations import LocationRepository
from app.repositories.onboarding import OnboardingRepository
from app.repositories.preferences import PreferenceRepository
from app.repositories.users import UserRepository
from app.services.adaptive_preferences import build_deterministic_adaptive_profile

MIN_QUESTIONS = 5
TARGET_QUESTIONS = (7, 8)
MAX_QUESTIONS = 8
MAX_CLARIFICATION_QUESTIONS = 1
DESTINATION_SELECTOR_KEYS = frozenset({"destination_branch"})
DESTINATION_BRANCH_BY_KEY = {
    "beach_activity": "beach_coast",
    "beach_proximity": "beach_coast",
    "mountain_outdoors": "mountains_hills",
    "mountain_scenery": "mountains_hills",
    "mountain_recovery": "mountains_hills",
    "city_focus": "city_town",
    "city_location": "city_town",
    "rural_isolation": "countryside_rural",
    "rural_stay_style": "countryside_rural",
}
DESTINATION_MATCHING_KEYS = frozenset(
    {
        "destination_detail",
        "destination_preference",
        "destination_matching",
        "beach_activity",
        "beach_proximity",
        "mountain_outdoors",
        "mountain_scenery",
        "mountain_recovery",
        "city_focus",
        "city_location",
        "rural_isolation",
        "rural_stay_style",
    }
)
GROUP_BRANCH_KEYS = frozenset({"family_needs", "couple_style", "friends_vibe"})
COUPLE_SIGNAL_VALUES = frozenset({"romance", "adventure", "decompression", "relaxation"})


class OnboardingService:
    """Own session ownership, Claude question generation, and adaptive persistence."""

    def __init__(
        self,
        session: AsyncSession,
        adaptive_provider: AdaptiveOnboardingProvider | None = None,
    ) -> None:
        self._session = session
        self._onboarding = OnboardingRepository(session)
        self._adaptive_provider = adaptive_provider

    async def start(
        self, user_id: uuid.UUID, version: int | None, adaptive: bool = True
    ) -> OnboardingSessionRead:
        """Resume or create an ADAPTIVE session with the deterministic Q1."""
        del version, adaptive
        self._require_claude()
        active = await self._onboarding.active_session(user_id, for_update=True)
        if active is not None:
            if active.mode != "ADAPTIVE":
                active.status = "RESTART_REQUIRED"
                active.completion_reason = "LEGACY_STATIC_SESSION_RESTART_REQUIRED"
                await self._session.commit()
            else:
                if active.generation_state == "GENERATING":
                    return await self._session_read(active)
                if await self._onboarding.session_questions(active.id):
                    return await self._session_read(active)
                return await self._generate_first(user_id, active.id)

        session = await self._onboarding.create_session(user_id)
        session.ai_model = self._adaptive_provider.model  # type: ignore[union-attr]
        session.prompt_version = self._adaptive_provider.prompt_version  # type: ignore[union-attr]
        return await self._generate_first(user_id, session.id)

    def _require_claude(self) -> None:
        """Reject every non-Claude adaptive provider before a session is created."""
        if self._adaptive_provider is None or self._adaptive_provider.provider_name != "anthropic":
            raise DependencyUnavailableError(
                "Claude is required for adaptive onboarding question generation"
            )

    async def questions(
        self, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> list[OnboardingQuestionRead]:
        """Return generated question history with answers."""
        session = await self._owned(user_id, session_id)
        self._require_adaptive_session(session)
        generated = await self._onboarding.session_questions(session.id)
        answer_map = {
            question.id: answer.answer
            for question, answer in await self._onboarding.adaptive_answer_rows(session.id)
        }
        return [self._generated_read(item, answer_map.get(item.id)) for item in generated]

    async def submit_answers(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        payload: OnboardingAnswersRequest,
    ) -> OnboardingSessionRead:
        """Submit one idempotent adaptive answer and generate the next Claude question."""
        session = await self._owned(user_id, session_id)
        self._require_adaptive_session(session)
        self._require_claude()
        if len(payload.answers) != 1:
            raise ImportValidationError("Adaptive onboarding accepts one answer at a time")
        return await self._submit_adaptive(user_id, session_id, payload)

    async def status(self, user_id: uuid.UUID, completed: bool) -> OnboardingStatusRead:
        """Return adaptive progress without claiming a fixed total."""
        session = await self._onboarding.active_session(user_id)
        if session is None:
            return OnboardingStatusRead(
                completed=completed,
                skipped=not completed and await self._was_skipped(user_id),
                active_session_id=None,
                answered_count=0,
                question_count=0,
                mode="ADAPTIVE",
            )
        if session.mode != "ADAPTIVE":
            return OnboardingStatusRead(
                completed=completed,
                skipped=False,
                active_session_id=None,
                answered_count=0,
                question_count=0,
                mode="ADAPTIVE",
                completion_reason="LEGACY_STATIC_SESSION_RESTART_REQUIRED",
            )
        answered, total = await self._onboarding.counts(session)
        return OnboardingStatusRead(
            completed=completed,
            skipped=False,
            active_session_id=session.id,
            answered_count=answered,
            question_count=total,
            mode=session.mode,
            generation_state=session.generation_state,
            completion_reason=session.completion_reason,
        )

    async def skip(self, user_id: uuid.UUID) -> OnboardingSkipRead:
        """Record an explicit skip without completing the adaptive questionnaire."""
        user = await UserRepository(self._session).get_by_id(user_id, for_update=True)
        if user is None:
            raise UserNotFoundError()
        if user.onboarding_completed:
            raise ImportValidationError("Onboarding is already completed")

        session = await self._onboarding.active_session(user_id, for_update=True)
        if session is None and user.onboarding_skipped_at is not None:
            session = await self._onboarding.latest_skipped_session(user_id)
        previous_status = session.status if session is not None else None
        if session is not None:
            session.status = "SKIPPED"
            session.generation_state = "IDLE"
            session.completion_reason = "USER_SKIPPED"
        if user.onboarding_skipped_at is None:
            user.onboarding_skipped_at = datetime.now(UTC)
        await self._session.commit()
        get_logger().info(
            "onboarding_skipped",
            user_id=str(user_id),
            session_id=str(session.id) if session is not None else None,
            previous_status=previous_status,
        )
        return OnboardingSkipRead(
            skipped=True,
            completed=False,
            session_id=session.id if session is not None else None,
            status="SKIPPED",
        )

    async def _was_skipped(self, user_id: uuid.UUID) -> bool:
        user = await UserRepository(self._session).get_by_id(user_id)
        return bool(user is not None and user.onboarding_skipped_at is not None)

    async def owned_session(self, user_id: uuid.UUID, session_id: uuid.UUID) -> OnboardingSession:
        """Return an owned session for route-level completion."""
        return await self._owned(user_id, session_id)

    async def adaptive_complete(self, user_id: uuid.UUID, session_id: uuid.UUID) -> dict[str, Any]:
        """Deterministically persist the final profile for an adaptive session."""
        session = await self._owned(user_id, session_id, for_update=True)
        self._require_adaptive_session(session)
        if session.status == "SKIPPED":
            raise ImportValidationError("Skipped onboarding sessions cannot be completed")
        if session.status == "COMPLETED":
            payload = session.profile_payload or {}
            return {
                "preferences_created": int(payload.get("resolved_preference_count", 0)),
                "profile": payload.get("profile", {}),
                "unresolved_attributes": payload.get("unresolved_attributes", []),
            }
        if session.generation_state == "PROFILE_GENERATING":
            raise DependencyUnavailableError("Profile generation is already in progress")
        answered, _ = await self._onboarding.counts(session)
        if answered < MIN_QUESTIONS and session.status != "READY_TO_COMPLETE":
            raise ImportValidationError(
                f"Answer at least {MIN_QUESTIONS} adaptive questions before completing"
            )
        rows = await self._onboarding.adaptive_answer_rows(session.id)
        readiness = self._completion_readiness(session, rows=rows, answered_count=answered)
        if not readiness["ready"]:
            raise ImportValidationError(
                "Adaptive onboarding is missing required destination or group signals"
            )
        session.generation_state = "PROFILE_GENERATING"
        await self._session.commit()
        get_logger().info(
            "adaptive_deterministic_profile_started",
            question_position=answered,
        )
        try:
            profile = build_deterministic_adaptive_profile(
                rows,
                self._location_context(session),
                self._routing_state(session),
            )
            attributes = AttributeRepository(self._session)
            preferences = PreferenceRepository(self._session)
            resolved = 0
            unresolved = list(profile.unresolved_attributes)
            for item in profile.preferences:
                attribute = await attributes.resolve_onboarding_attribute(item["attribute_slug"])
                if attribute is None:
                    if item["attribute_slug"] not in unresolved:
                        unresolved.append(item["attribute_slug"])
                    get_logger().warning(
                        "onboarding_attribute_unresolved",
                        session_id=str(session.id),
                        attribute_slug=item["attribute_slug"],
                    )
                    continue
                await preferences.upsert(
                    user_id,
                    attribute["id"],
                    item["importance_weight"],
                    item["preferred_value"],
                    PreferenceSource.ONBOARDING_DETERMINISTIC,
                )
                resolved += 1
        except Exception as exc:
            await self._mark_generation_failed(session.id)
            get_logger().warning(
                "adaptive_deterministic_profile_failed",
                question_position=answered,
                error_type=type(exc).__name__,
                error_message="deterministic profile construction failed",
            )
            raise DependencyUnavailableError(
                "Adaptive onboarding deterministic profile generation failed"
            ) from exc
        get_logger().info(
            "adaptive_deterministic_profile_succeeded",
            question_position=answered,
        )
        user = await UserRepository(self._session).get_by_id(user_id)
        if user is None:
            raise UserNotFoundError()
        session = await self._owned(user_id, session_id, for_update=True)
        session.status = "COMPLETED"
        session.completed_at = datetime.now(UTC)
        session.generation_state = "IDLE"
        session.completion_reason = session.completion_reason or "DETERMINISTIC_PROFILE_GENERATED"
        session.profile_summary = profile.profile_summary
        session.profile_confidence = profile.confidence
        profile_payload = {
            "profile_summary": profile.profile_summary,
            "confidence": profile.confidence,
            "context": profile.context,
            "preferences": profile.preferences,
            "signals": profile.signals,
        }
        session.profile_payload = {
            "profile": profile_payload,
            "resolved_preference_count": resolved,
            "unresolved_attributes": unresolved,
            "location_context": self._location_context(session),
            "routing_state": self._routing_state(session),
        }
        user.onboarding_completed = True
        user.onboarding_skipped_at = None
        await self._session.commit()
        get_logger().info(
            "adaptive_onboarding_completed",
            session_id=str(session.id),
            user_id=str(user_id),
            resolved_attributes=resolved,
            unresolved_attributes=len(unresolved),
            completion_reason=session.completion_reason,
        )
        return {
            "preferences_created": resolved,
            "profile": profile_payload,
            "unresolved_attributes": unresolved,
        }

    async def _generate_first(
        self, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> OnboardingSessionRead:
        """Persist the deterministic location question without invoking a provider."""
        session = await self._owned(user_id, session_id, for_update=True)
        if not await self._onboarding.session_questions(session.id):
            await self._onboarding.add_session_question(
                session.id,
                1,
                self._question_values(
                    self._location_question(),
                    provider_name="deterministic",
                    ai_model="deterministic-location-v1",
                ),
            )
        session.generation_state = "IDLE"
        await self._session.commit()
        return await self._session_read(session)

    async def _submit_adaptive(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        payload: OnboardingAnswersRequest,
    ) -> OnboardingSessionRead:
        session = await self._owned(user_id, session_id, for_update=True)
        if session.status == "SKIPPED":
            raise ImportValidationError("Skipped onboarding sessions cannot accept answers")
        if session.status == "COMPLETED":
            raise ImportValidationError("Completed onboarding sessions are immutable")
        if session.status == "READY_TO_COMPLETE":
            raise ImportValidationError("Onboarding is ready to complete")
        if session.generation_state == "GENERATING":
            raise DependencyUnavailableError("Adaptive question generation is already in progress")
        item = payload.answers[0]
        question = await self._onboarding.session_question(session.id, item.question_id)
        if question is None:
            raise ImportValidationError("Generated question does not belong to this session")
        self._validate_answer(question, item.answer)
        existing = {
            row.session_question_id: row
            for row in await self._onboarding.answers(session.id)
            if row.session_question_id is not None
        }.get(question.id)
        if (
            existing is not None
            and existing.answer == item.answer
            and session.generation_state != "FAILED"
        ):
            return await self._session_read(session)
        if existing is not None and existing.answer != item.answer:
            await self._onboarding.delete_questions_after(session.id, question.position)
            payload = dict(session.profile_payload or {})
            payload.pop("routing_state", None)
            session.profile_payload = payload
        await self._onboarding.upsert_answer(
            session.id, None, item.answer, session_question_id=question.id
        )
        if self._is_location_question(question):
            await self._persist_location_context(session, str(item.answer["value"]))
        session.generation_state = "GENERATING"
        await self._session.commit()

        session = await self._owned(user_id, session_id)
        answered, _ = await self._onboarding.counts(session)
        if answered > MAX_QUESTIONS:
            rows = await self._onboarding.adaptive_answer_rows(session.id)
            readiness = self._completion_readiness(session, rows=rows, answered_count=answered)
            if not readiness["ready"]:
                await self._mark_generation_failed(session_id)
                raise DependencyUnavailableError(
                    "Claude adaptive onboarding provider failed before canonical signals"
                )
            return await self._finish_questionnaire(session_id, "MAX_QUESTIONS")
        existing_questions = await self._onboarding.session_questions(session.id)
        answered_rows = await self._onboarding.adaptive_answer_rows(session.id)
        persisted_readiness = self._completion_readiness(
            session, rows=answered_rows, answered_count=answered
        )
        if answered >= MIN_QUESTIONS and persisted_readiness["ready"]:
            return await self._finish_questionnaire(session_id, "CANONICAL_SIGNALS_COMPLETE")
        clarification_count = sum(
            item.question_kind == "clarification" for item in existing_questions
        )
        context = await self._context(session)
        question_position = answered + 1
        get_logger().info(
            "adaptive_question_generation_started",
            provider=self._adaptive_provider.provider_name,
            model=self._adaptive_provider.model,
            question_position=question_position,
        )
        try:
            decision = await self._adaptive_provider.generate_next_question(context)  # type: ignore[union-attr]
            self._record_inferred_signals(session, decision.inferred_signals)
            readiness = (await self._context(session))["completion_gate"]
            if answered >= MIN_QUESTIONS and readiness["ready"]:
                get_logger().info(
                    "adaptive_question_generation_succeeded",
                    provider=self._adaptive_provider.provider_name,
                    model=self._adaptive_provider.model,
                    question_position=question_position,
                )
                return await self._finish_questionnaire(
                    session_id,
                    (
                        decision.completion_reason
                        if decision.questionnaire_complete
                        else "CANONICAL_SIGNALS_COMPLETE"
                    ),
                )
            if decision.questionnaire_complete:
                if answered < MIN_QUESTIONS:
                    raise AdaptiveProviderError("Provider stopped below the minimum question count")
                if not readiness["ready"]:
                    # Claude must decide the next question. Give it the explicit blockers so
                    # an over-eager completion response cannot turn into a local fallback.
                    corrective_context = await self._context(session)
                    corrective_context["completion_gate"] = readiness
                    corrective_context["completion_gate"]["instruction"] = (
                        "Completion is blocked. Ask one meaningful question that captures the "
                        "highest-priority missing canonical signal; do not declare completion."
                    )
                    decision = await self._adaptive_provider.generate_next_question(
                        corrective_context
                    )  # type: ignore[union-attr]
                    self._record_inferred_signals(session, decision.inferred_signals)
                    if decision.questionnaire_complete:
                        readiness = (await self._context(session))["completion_gate"]
                        if not readiness["ready"]:
                            raise AdaptiveProviderError(
                                "Provider stopped before canonical destination and group signals"
                            )
                if decision.questionnaire_complete:
                    get_logger().info(
                        "adaptive_question_generation_succeeded",
                        provider=self._adaptive_provider.provider_name,
                        model=self._adaptive_provider.model,
                        question_position=question_position,
                    )
                    return await self._finish_questionnaire(
                        session_id, decision.completion_reason or "SUFFICIENT_SIGNAL"
                    )
            if decision.question is None:
                raise AdaptiveProviderError("Provider omitted the next question")
            if answered >= MAX_QUESTIONS and decision.question.question_kind != "clarification":
                raise AdaptiveProviderError(
                    "Provider attempted to exceed the canonical eight-question flow"
                )
            if (
                decision.question.question_kind == "clarification"
                and clarification_count >= MAX_CLARIFICATION_QUESTIONS
            ):
                raise AdaptiveProviderError("Provider exceeded the clarification limit")
            try:
                self._validate_question(decision.question, existing_questions, answered_rows)
            except DuplicateAdaptiveQuestionError:
                # A duplicate is a normal corrective-routing event, not a provider outage.
                # Give Claude only the safe canonical feedback needed to choose a missing topic
                # or declare completion when the gate is satisfied.
                corrective_context = await self._context(session)
                corrective_context["question_feedback"] = (
                    "This topic is already answered. Ask only for a missing canonical signal, "
                    "or declare completion if all required signals are present."
                )
                replacement = await self._adaptive_provider.generate_next_question(
                    corrective_context
                )  # type: ignore[union-attr]
                self._record_inferred_signals(session, replacement.inferred_signals)
                readiness = (await self._context(session))["completion_gate"]
                if answered >= MIN_QUESTIONS and readiness["ready"]:
                    return await self._finish_questionnaire(
                        session_id,
                        (
                            replacement.completion_reason
                            if replacement.questionnaire_complete
                            else "CANONICAL_SIGNALS_COMPLETE"
                        ),
                    )
                if replacement.questionnaire_complete:
                    raise AdaptiveProviderError(
                        "Provider stopped before missing canonical signals were captured"
                    ) from None
                if replacement.question is None:
                    raise AdaptiveProviderError("Provider omitted the next question") from None
                self._validate_question(replacement.question, existing_questions, answered_rows)
                decision = replacement
            return await self._persist_next_question(
                session_id, decision.question, question_position
            )
        except AdaptiveProviderError as exc:
            await self._mark_generation_failed(session_id)
            get_logger().warning(
                "adaptive_question_generation_failed",
                provider=self._adaptive_provider.provider_name,
                model=self._adaptive_provider.model,
                question_position=question_position,
                error_type=type(exc).__name__,
                error_message=str(exc),
                cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None,
            )
            raise DependencyUnavailableError(exc.public_message) from exc

    async def _persist_next_question(
        self, session_id: uuid.UUID, question: AdaptiveQuestion, question_position: int
    ) -> OnboardingSessionRead:
        session = await self._onboarding.get_session(session_id)
        if session is None:
            raise OnboardingSessionNotFoundError()
        session = await self._owned(session.user_id, session_id, for_update=True)
        if session.status == "SKIPPED":
            return await self._session_read(session)
        existing = await self._onboarding.session_questions(session.id)
        if not any(item.prompt == question.prompt for item in existing):
            await self._onboarding.add_session_question(
                session.id,
                await self._onboarding.next_session_question_position(session.id),
                self._question_values(question),
            )
        session.generation_state = "IDLE"
        await self._session.commit()
        get_logger().info(
            "adaptive_question_generation_succeeded",
            provider=self._adaptive_provider.provider_name if self._adaptive_provider else None,
            model=self._adaptive_provider.model if self._adaptive_provider else None,
            question_position=question_position,
        )
        return await self._session_read(session)

    async def _finish_questionnaire(
        self, session_id: uuid.UUID, reason: str
    ) -> OnboardingSessionRead:
        session = await self._onboarding.get_session(session_id)
        if session is None:
            raise OnboardingSessionNotFoundError()
        session = await self._owned(session.user_id, session_id, for_update=True)
        if session.status == "SKIPPED":
            return await self._session_read(session)
        session.status = "READY_TO_COMPLETE"
        session.generation_state = "IDLE"
        session.completion_reason = reason
        await self._session.commit()
        get_logger().info(
            "adaptive_questionnaire_ready",
            session_id=str(session.id),
            answered_count=(await self._onboarding.counts(session))[0],
            completion_reason=reason,
        )
        return await self._session_read(session)

    async def _context(self, session: OnboardingSession) -> dict[str, Any]:
        rows = await self._onboarding.adaptive_answer_rows(session.id)
        questions = await self._onboarding.session_questions(session.id)
        answers_by_question = {question.id: answer.answer for question, answer in rows}
        attributes = await AttributeRepository(self._session).list_attributes()
        destination = self._location_context(session)
        covered_dimensions = sorted(
            {dimension for item in questions for dimension in item.semantic_dimensions}
        )
        known_profile_signals = self._routing_signals(rows, destination)
        known_profile_signals["inferred_signals"] = self._routing_state(session).get(
            "inferred_signals", {}
        )
        return {
            "session_id": str(session.id),
            "question_count": len(questions),
            "answered_count": len(rows),
            "min_questions": MIN_QUESTIONS,
            "target_questions": list(TARGET_QUESTIONS),
            "max_questions": MAX_QUESTIONS,
            "max_clarification_questions": MAX_CLARIFICATION_QUESTIONS,
            "destination": destination,
            "known_profile_signals": known_profile_signals,
            "previously_inferred_preferences": [
                preference
                for _, answer in rows
                for preference in (answer.interpreted_preferences or [])
            ],
            "semantic_dimensions_covered": covered_dimensions,
            "questions": [
                {
                    "question_id": str(item.id),
                    "position": item.position,
                    "prompt": item.prompt,
                    "question_kind": item.question_kind,
                    "question_key": item.generation_metadata.get("question_key"),
                    "semantic_dimensions": item.semantic_dimensions,
                    "answer": self._answer_with_labels(item, answers_by_question.get(item.id)),
                }
                for item in questions
            ],
            "answers": [
                {
                    "question_id": str(question.id),
                    "question_kind": question.question_kind,
                    "question_key": question.generation_metadata.get("question_key"),
                    "answer": self._answer_with_labels(question, answer.answer),
                }
                for question, answer in rows
            ],
            "routing_policy": {
                "q1": "deterministic_location_text",
                "q2": "travel_group_normally",
                "universal_core": ["price_value", "travel_pace", "food_preference"],
                "destination_branches": [
                    "beach_coast",
                    "mountains_hills",
                    "city_town",
                    "countryside_rural",
                ],
                "max_destination_branches": 1,
                "max_group_branch_questions": 1,
                "solo_group_branch_questions": 0,
                "normal_question_range": [6, 8],
                "clarification_exception": (
                    "one additional question only for material ambiguity or contradiction"
                ),
            },
            "current_branch_state": self._branch_state(rows),
            "completion_gate": self._completion_readiness(
                session, rows=rows, answered_count=len(rows)
            ),
            "unanswered_required_dimensions": self._unanswered_dimensions(rows),
            "allowed_attributes": [
                {"slug": str(item["slug"]), "name": str(item["name"])} for item in attributes
            ],
        }

    @staticmethod
    def _answer_with_labels(
        question: OnboardingSessionQuestion, answer: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Add human-readable option labels without changing the persisted answer."""
        if answer is None or not isinstance(answer, dict):
            return answer
        selected = answer.get("selected")
        selected_values = selected if isinstance(selected, list) else [selected]
        labels = {
            str(option.get("value")): str(option.get("label"))
            for option in (question.options or [])
            if option.get("value") is not None and option.get("label") is not None
        }
        selected_labels = [labels[str(value)] for value in selected_values if str(value) in labels]
        if not selected_labels:
            return answer
        enriched = dict(answer)
        enriched["selected_labels"] = selected_labels
        return enriched

    @staticmethod
    def _routing_signals(
        rows: list[tuple[OnboardingSessionQuestion, Any]],
        destination: dict[str, Any],
    ) -> dict[str, Any]:
        """Expose conservative routing signals inferred from explicit answers only."""
        state = OnboardingService._branch_state(rows)
        signals: dict[str, Any] = {
            "destination": destination,
            "answered_question_keys": [],
            "group_type": state["group_type"],
            "destination_branch": state["destination_branch"],
            "destination_branch_selected": state["destination_branch_selected"],
            "destination_matching_signal_captured": state["destination_matching_signal_captured"],
            "group_signal_captured": state["group_signal_captured"],
        }
        for question, answer in rows:
            key = OnboardingService._canonical_question_key(question, answer.answer)
            signals["answered_question_keys"].append(key or None)
            selected = answer.answer.get("selected") if isinstance(answer.answer, dict) else None
            value = selected[0] if isinstance(selected, list) and selected else selected
            if key == "travel_group" and value:
                signals["group_type"] = str(value)
        signals["inferred_signals"] = OnboardingService._routing_state_from_rows(rows)
        return signals

    @staticmethod
    def _routing_state_from_rows(
        rows: list[tuple[OnboardingSessionQuestion, Any]],
    ) -> dict[str, Any]:
        """Expose answer-attached inference without treating a clarification as proof."""
        inferred: dict[str, Any] = {}
        for _, answer in rows:
            for signal in answer.interpreted_preferences or []:
                if not isinstance(signal, dict):
                    continue
                routing_key = signal.get("routing_key")
                if routing_key in DESTINATION_MATCHING_KEYS | GROUP_BRANCH_KEYS:
                    inferred[str(routing_key)] = signal
        return inferred

    @staticmethod
    def _branch_state(rows: list[tuple[OnboardingSessionQuestion, Any]]) -> dict[str, Any]:
        """Describe branch coverage while separating routing from matching signal."""
        destination_questions = 0
        destination_matching_questions = 0
        group_questions = 0
        clarification_questions = 0
        destination_branch: str | None = None
        group_type: str | None = None
        for question, answer in rows:
            key = OnboardingService._canonical_question_key(question, answer.answer)
            if question.question_kind == "clarification":
                clarification_questions += 1
            value = OnboardingService._answer_value(answer.answer)
            if key in DESTINATION_SELECTOR_KEYS:
                destination_questions += 1
                if OnboardingService._canonical_destination_branch(value):
                    destination_branch = OnboardingService._canonical_destination_branch(value)
            if key in DESTINATION_BRANCH_BY_KEY:
                destination_branch = DESTINATION_BRANCH_BY_KEY[key]
            if OnboardingService._is_destination_matching_question(question, answer.answer):
                destination_matching_questions += 1
            if key == "travel_group" and value:
                group_type = str(value)
            if key in GROUP_BRANCH_KEYS:
                group_questions += 1
        group_key = OnboardingService._group_branch_key(group_type)
        group_signal_captured = OnboardingService._group_signal_captured(group_key, rows)
        if group_key is None and group_type == "solo":
            group_signal_captured = True
        return {
            "destination_branch_questions_asked": destination_questions,
            "destination_branch_selected": destination_branch is not None,
            "destination_branch": destination_branch,
            "destination_matching_questions_asked": destination_matching_questions,
            "destination_matching_signal_captured": destination_matching_questions > 0,
            "group_branch_questions_asked": group_questions,
            "group_branch_key": group_key,
            "group_type": group_type,
            "group_signal_captured": group_signal_captured,
            "destination_branch_active": destination_branch is not None,
            "clarification_questions_asked": clarification_questions,
        }

    @staticmethod
    def _answer_value(answer: dict[str, Any] | None) -> str | None:
        if not isinstance(answer, dict):
            return None
        selected = answer.get("selected")
        if isinstance(selected, list):
            selected = selected[0] if selected else None
        value = selected if selected is not None else answer.get("value")
        return str(value) if value is not None else None

    @staticmethod
    def _canonical_destination_branch(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
        aliases = {
            "beach": "beach_coast",
            "coast": "beach_coast",
            "beach_coast": "beach_coast",
            "hills": "mountains_hills",
            "mountains": "mountains_hills",
            "mountains_hills": "mountains_hills",
            "city": "city_town",
            "town": "city_town",
            "city_town": "city_town",
            "rural": "countryside_rural",
            "countryside": "countryside_rural",
            "countryside_rural": "countryside_rural",
        }
        return aliases.get(normalized)

    @staticmethod
    def _is_destination_matching_question(
        question: OnboardingSessionQuestion,
        answer: dict[str, Any] | None = None,
    ) -> bool:
        """Only direct branch-detail questions satisfy destination matching coverage."""
        key = OnboardingService._canonical_question_key(question, answer)
        return key in DESTINATION_MATCHING_KEYS

    @staticmethod
    def _canonical_question_key(
        question: OnboardingSessionQuestion,
        answer: dict[str, Any] | None = None,
    ) -> str | None:
        """Return a canonical key, with narrow compatibility for older persisted questions."""
        raw_key = str(question.generation_metadata.get("question_key") or "")
        if raw_key in CANONICAL_ADAPTIVE_QUESTION_KEYS:
            return raw_key
        if raw_key in {"group_type", "travel_group"}:
            return "travel_group"
        if raw_key == "destination_type":
            return "destination_branch"

        dimensions = {
            str(item).strip().casefold()
            for item in (question.semantic_dimensions or [])
            if str(item).strip()
        }
        value = OnboardingService._answer_value(answer)
        if dimensions == {"couple_style"} and value:
            normalized = value.casefold().replace(" ", "_")
            if normalized in COUPLE_SIGNAL_VALUES:
                return "couple_style"
        group_dimensions = {"group_type", "travel_group", "travel_context"}
        if (
            dimensions
            and dimensions <= group_dimensions
            and value
            and OnboardingService._group_branch_key(value) != "group_type_unknown"
        ):
            return "travel_group"
        if dimensions == {"travel_group"}:
            return "travel_group"
        if dimensions and dimensions <= {
            "price",
            "price_value",
            "budget_sensitivity",
            "value_expectation",
        }:
            return "price_value"
        if dimensions and dimensions <= {"activities", "travel_pace", "activity_level"}:
            return "travel_pace"
        if dimensions and dimensions <= {
            "food",
            "food_preference",
            "dining_preference",
        }:
            return "food_preference"
        if dimensions == {"price"}:
            return "price_value"
        if dimensions == {"activities"}:
            return "travel_pace"
        if dimensions == {"food"}:
            return "food_preference"
        if dimensions == {"urban_location"}:
            return "city_location"
        if dimensions and dimensions <= DESTINATION_MATCHING_KEYS and len(dimensions) == 1:
            return next(iter(dimensions))
        if len(dimensions) == 1 and next(iter(dimensions), "") in DESTINATION_BRANCH_BY_KEY:
            return next(iter(dimensions))
        return None

    @staticmethod
    def _group_branch_key(group_type: str | None) -> str | None:
        normalized = (group_type or "").casefold().replace(" ", "_")
        if normalized == "couple":
            return "couple_style"
        if normalized in {
            "family",
            "family_with_kids",
            "family_with_children",
            "family_kids",
        }:
            return "family_needs"
        if normalized in {"friends", "friends_group"}:
            return "friends_vibe"
        if normalized in {"solo", "solo_traveller", "solo_traveler"}:
            return None
        return "group_type_unknown"

    @staticmethod
    def _group_signal_captured(
        group_key: str | None,
        rows: list[tuple[OnboardingSessionQuestion, Any]],
    ) -> bool:
        if group_key is None:
            return False
        if group_key == "group_type_unknown":
            return False
        for question, answer in rows:
            key = OnboardingService._canonical_question_key(question, answer.answer)
            if key != group_key:
                continue
            value = OnboardingService._answer_value(answer.answer)
            if group_key == "couple_style":
                normalized = (value or "").casefold().replace(" ", "_")
                return normalized in COUPLE_SIGNAL_VALUES
            return bool(value)
        return False

    @staticmethod
    def _routing_state(session: OnboardingSession) -> dict[str, Any]:
        payload = session.profile_payload or {}
        state = payload.get("routing_state")
        return dict(state) if isinstance(state, dict) else {}

    @staticmethod
    def _record_inferred_signals(
        session: OnboardingSession, signals: list[AdaptiveInferredSignal]
    ) -> None:
        if not signals:
            return
        payload = dict(session.profile_payload or {})
        routing = dict(payload.get("routing_state") or {})
        inferred = dict(routing.get("inferred_signals") or {})
        for signal in signals:
            inferred[signal.routing_key] = signal.model_dump(mode="json")
        routing["inferred_signals"] = inferred
        payload["routing_state"] = routing
        session.profile_payload = payload

    @staticmethod
    def _completion_readiness(
        session: OnboardingSession,
        rows: list[tuple[OnboardingSessionQuestion, Any]] | None = None,
        answered_count: int | None = None,
    ) -> dict[str, Any]:
        """Enforce downstream branch coverage before honoring Claude completion."""
        rows = rows or []
        state = OnboardingService._branch_state(rows)
        raw_inferred = OnboardingService._routing_state(session).get("inferred_signals")
        inferred = raw_inferred if isinstance(raw_inferred, dict) else {}
        inferred_keys = {
            key
            for key, signal in inferred.items()
            if OnboardingService._usable_inferred_signal(signal)
        }
        inferred_branch = inferred.get("destination_branch")
        if OnboardingService._usable_inferred_signal(
            inferred_branch
        ) and OnboardingService._canonical_destination_branch(
            str(inferred_branch.get("value") or "")
        ):
            state["destination_branch_selected"] = True
        if any(key in inferred_keys for key in DESTINATION_MATCHING_KEYS):
            state["destination_matching_signal_captured"] = True
        group_key = state.get("group_branch_key")
        if group_key in inferred_keys and group_key != "couple_style":
            state["group_signal_captured"] = True
        if group_key == "couple_style":
            inferred_couple = inferred.get("couple_style")
            if OnboardingService._usable_inferred_signal(inferred_couple):
                value = str(inferred_couple.get("value") or "").casefold().replace(" ", "_")
                state["group_signal_captured"] = (
                    state["group_signal_captured"] or value in COUPLE_SIGNAL_VALUES
                )
        blockers: list[str] = []
        if answered_count is not None and answered_count < MIN_QUESTIONS:
            blockers.append("minimum_question_count")
        if not OnboardingService._location_answered(rows):
            blockers.append("destination_context")
        if not state.get("group_type") or state.get("group_branch_key") == "group_type_unknown":
            blockers.append("travel_group")
        if state.get("group_branch_key") and not state.get("group_signal_captured"):
            blockers.append(str(state["group_branch_key"]))
        if not state.get("destination_branch_selected"):
            blockers.append("destination_branch")
        if not state.get("destination_matching_signal_captured"):
            blockers.append("destination_matching_signal")
        blockers.extend(OnboardingService._unanswered_dimensions(rows))
        return {
            "ready": not blockers,
            "blockers": list(dict.fromkeys(blockers)),
            "branch_state": state,
            "inferred_signals": inferred,
        }

    @staticmethod
    def _usable_inferred_signal(signal: Any) -> bool:
        if not isinstance(signal, dict):
            return False
        try:
            return float(signal.get("confidence", 0.0)) >= 0.85
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _location_answered(
        rows: list[tuple[OnboardingSessionQuestion, Any]],
    ) -> bool:
        return any(
            question.generation_metadata.get("code") == "adaptive_location" for question, _ in rows
        )

    @staticmethod
    def _unanswered_dimensions(rows: list[tuple[OnboardingSessionQuestion, Any]]) -> list[str]:
        """Return canonical core keys not yet represented by an answered question."""
        key_set = {
            OnboardingService._canonical_question_key(question, answer.answer)
            for question, answer in rows
        }
        required = [
            ("price_value", "price_value"),
            ("travel_pace", "travel_pace"),
            ("food_preference", "food_preference"),
        ]
        return [name for name, key in required if key not in key_set]

    async def _mark_generation_failed(self, session_id: uuid.UUID) -> None:
        session = await self._onboarding.get_session(session_id)
        if session is not None:
            session = await self._owned(session.user_id, session_id, for_update=True)
            if session.status != "SKIPPED":
                session.generation_state = "FAILED"
            await self._session.commit()

    async def _persist_location_context(self, session: OnboardingSession, raw_text: str) -> None:
        """Persist raw destination text and best-effort canonical resolution."""
        normalized = " ".join(raw_text.split()).casefold()
        resolved = await LocationRepository(self._session).resolve_text(raw_text)
        location: dict[str, Any] = {
            "raw_input": raw_text,
            "normalized_location_text": normalized,
            "country_id": None,
            "region_id": None,
            "city_id": None,
            "display_name": None,
            "resolution_status": "unresolved",
            "resolution_confidence": 0.0,
        }
        if resolved is not None:
            location.update(
                {
                    key: str(value) if isinstance(value, uuid.UUID) else value
                    for key, value in resolved.items()
                }
            )
        payload = dict(session.profile_payload or {})
        payload["location_context"] = location
        session.profile_payload = payload

    def _location_context(self, session: OnboardingSession) -> dict[str, Any]:
        """Return persisted destination context without inventing identifiers."""
        payload = session.profile_payload or {}
        location = payload.get("location_context")
        return dict(location) if isinstance(location, dict) else {}

    async def _owned(
        self, user_id: uuid.UUID, session_id: uuid.UUID, for_update: bool = False
    ) -> OnboardingSession:
        query = select(OnboardingSession).where(
            OnboardingSession.id == session_id,
            OnboardingSession.user_id == user_id,
        )
        if for_update:
            query = query.execution_options(populate_existing=True).with_for_update()
        session = await self._session.scalar(query)
        if session is None:
            raise OnboardingSessionNotFoundError()
        return session

    @staticmethod
    def _require_adaptive_session(session: OnboardingSession) -> None:
        """Prevent legacy STATIC records from entering the adaptive flow."""
        if session.mode != "ADAPTIVE":
            raise ImportValidationError("This legacy STATIC onboarding session must be restarted")

    async def _session_read(self, session: OnboardingSession) -> OnboardingSessionRead:
        answered, total = await self._onboarding.counts(session)
        current = None
        if session.mode == "ADAPTIVE":
            generated = await self._onboarding.session_questions(session.id)
            answers = await self._onboarding.answers(session.id)
            answer_ids = {
                answer.session_question_id
                for answer in answers
                if answer.session_question_id is not None
            }
            candidate = next((item for item in generated if item.id not in answer_ids), None)
            candidate = candidate or (generated[-1] if generated else None)
            if candidate is not None:
                answer = next(
                    (item.answer for item in answers if item.session_question_id == candidate.id),
                    None,
                )
                current = self._generated_read(candidate, answer)
        return OnboardingSessionRead(
            id=session.id,
            question_set_id=session.question_set_id,
            status=session.status,
            answered_count=answered,
            question_count=total,
            mode=session.mode,
            generation_state=session.generation_state,
            completion_reason=session.completion_reason,
            current_question=current,
        )

    @staticmethod
    def _generated_read(
        item: OnboardingSessionQuestion, answer: dict[str, Any] | None
    ) -> OnboardingQuestionRead:
        return OnboardingQuestionRead(
            id=item.id,
            code=str(item.generation_metadata.get("code") or f"adaptive_{item.position}"),
            prompt=item.prompt,
            answer_type=item.answer_type,
            options=item.options,
            position=item.position,
            is_required=item.is_required,
            helper_text=item.helper_text,
            question_kind=item.question_kind,
            semantic_dimensions=item.semantic_dimensions,
            scale_min=float(item.scale_min) if item.scale_min is not None else None,
            scale_max=float(item.scale_max) if item.scale_max is not None else None,
            scale_labels=item.scale_labels,
            answer=answer,
        )

    @staticmethod
    def _location_question() -> AdaptiveQuestion:
        """Build the deterministic first question without provider involvement."""
        return AdaptiveQuestion(
            prompt="Where are you travelling to?",
            helper_text="Enter a city, region, or destination.",
            answer_type="text",
            required=True,
            question_kind="standard",
            question_key="adaptive_location",
            semantic_dimensions=["location"],
        )

    @staticmethod
    def _is_location_question(question: OnboardingSessionQuestion) -> bool:
        return question.generation_metadata.get("code") == "adaptive_location"

    def _question_values(
        self,
        question: AdaptiveQuestion,
        provider_name: str | None = None,
        ai_model: str | None = None,
    ) -> dict[str, Any]:
        provider = self._adaptive_provider
        location_question = question.question_key == "adaptive_location"
        return {
            "prompt": question.prompt,
            "helper_text": question.helper_text,
            "answer_type": question.answer_type,
            "options": [item.model_dump(mode="json") for item in question.options]
            if question.options
            else None,
            "scale_min": question.scale_min,
            "scale_max": question.scale_max,
            "scale_labels": question.scale_labels,
            "is_required": question.required,
            "question_kind": question.question_kind,
            "semantic_dimensions": question.semantic_dimensions,
            "provider": provider_name
            or (provider.provider_name if provider is not None else "adaptive"),
            "ai_model": ai_model or (provider.model if provider is not None else None),
            "prompt_version": provider.prompt_version if provider is not None else None,
            "generation_metadata": {
                "question_key": question.question_key,
                **({"code": "adaptive_location"} if location_question else {}),
            },
        }

    @staticmethod
    def _answered_canonical_topics(
        previous: list[Any],
        answered_rows: list[tuple[OnboardingSessionQuestion, Any]] | None,
    ) -> set[str]:
        """Collect canonical topics from answered rows, with legacy-question support."""
        if answered_rows is not None:
            topics: set[str] = set()
            for prior_question, answer in answered_rows:
                answer_value = (
                    answer.answer
                    if hasattr(answer, "answer")
                    else answer
                    if isinstance(answer, dict)
                    else None
                )
                key = OnboardingService._canonical_question_key(prior_question, answer_value)
                if key:
                    topics.add(key)
            return topics

        # Keep the helper useful for callers/tests that only have historical question rows.
        # The live service passes answered_rows so an unanswered generated question cannot
        # incorrectly block a topic.
        topics = set()
        for prior_question in previous:
            key = OnboardingService._canonical_question_key(
                prior_question, getattr(prior_question, "answer", None)
            )
            if key:
                topics.add(key)
        return topics

    @staticmethod
    def _validate_question(
        question: AdaptiveQuestion,
        previous: list[Any],
        answered_rows: list[tuple[OnboardingSessionQuestion, Any]] | None = None,
    ) -> None:
        if question.question_key == "adaptive_location" or question.prompt.strip().casefold() == (
            "where are you travelling to?"
        ):
            raise AdaptiveProviderError(
                "Claude may not replace the deterministic location question"
            )
        prompts = {str(item.prompt).strip().casefold() for item in previous}
        if question.prompt.strip().casefold() in prompts:
            raise DuplicateAdaptiveQuestionError("Provider repeated an existing question")
        covered_topics = OnboardingService._answered_canonical_topics(previous, answered_rows)
        if question.question_key and question.question_key in covered_topics:
            raise DuplicateAdaptiveQuestionError(
                "Provider repeated an answered canonical onboarding topic"
            )

    @staticmethod
    def _validate_answer(question: OnboardingSessionQuestion, answer: dict[str, Any]) -> None:
        options = {str(item.get("value")) for item in (question.options or [])}
        if question.answer_type == "single_choice":
            value = answer.get("selected")
            if isinstance(value, list):
                if len(value) != 1:
                    raise ImportValidationError("Select exactly one option")
                value = value[0]
            if not isinstance(value, str) or value not in options:
                raise ImportValidationError("Answer is not a valid question option")
        elif question.answer_type == "multi_choice":
            values = answer.get("selected")
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(value, str) or value not in options for value in values)
            ):
                raise ImportValidationError("Answer contains invalid options")
            if len(set(values)) != len(values) or len(values) > 8:
                raise ImportValidationError("Answer contains duplicate or excessive options")
        elif question.answer_type == "scale":
            value = answer.get("value")
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise ImportValidationError("Scale answer must be numeric")
            if question.scale_min is not None and value < float(question.scale_min):
                raise ImportValidationError("Scale answer is below the minimum")
            if question.scale_max is not None and value > float(question.scale_max):
                raise ImportValidationError("Scale answer is above the maximum")
        elif question.answer_type == "text":
            value = answer.get("value")
            if not isinstance(value, str) or not value.strip() or len(value) > 500:
                raise ImportValidationError("Text answer must contain 1-500 characters")
        else:
            raise ImportValidationError("Unsupported adaptive answer type")

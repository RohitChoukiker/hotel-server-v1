"""Onboarding session business rules and persistence."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.dto.onboarding import (
    OnboardingAnswersRequest,
    OnboardingQuestionRead,
    OnboardingSessionRead,
    OnboardingStatusRead,
)
from app.exceptions import ImportValidationError, OnboardingSessionNotFoundError
from app.models import OnboardingSession
from app.repositories.onboarding import OnboardingRepository


class OnboardingService:
    """Own questionnaire lifecycle and ownership checks."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._onboarding = OnboardingRepository(session)

    async def start(
        self, user_id: uuid.UUID, version: int | None
    ) -> OnboardingSessionRead:
        """Resume an in-progress session or create one on an active question set."""
        active = await self._onboarding.active_session(user_id)
        if active:
            return await self._session_read(active)
        question_set = await self._onboarding.active_question_set(version)
        if question_set is None:
            raise OnboardingSessionNotFoundError("No active onboarding question set")
        session = await self._onboarding.create_session(user_id, question_set.id)
        await self._session.commit()
        return await self._session_read(session)

    async def questions(
        self, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> list[OnboardingQuestionRead]:
        """Return ordered questions only to the session owner."""
        session = await self._owned(user_id, session_id)
        return [
            OnboardingQuestionRead.model_validate(item)
            for item in await self._onboarding.questions(session.question_set_id)
        ]

    async def submit_answers(
        self,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        payload: OnboardingAnswersRequest,
    ) -> OnboardingSessionRead:
        """Upsert a bounded batch of answers after question membership validation."""
        session = await self._owned(user_id, session_id)
        if session.status != "IN_PROGRESS":
            raise ImportValidationError("Completed onboarding sessions are immutable")
        question_ids = {
            item.id for item in await self._onboarding.questions(session.question_set_id)
        }
        for item in payload.answers:
            if item.question_id not in question_ids:
                raise ImportValidationError("Question does not belong to this session")
            await self._onboarding.upsert_answer(session.id, item.question_id, item.answer)
        await self._session.commit()
        return await self._session_read(session)

    async def status(self, user_id: uuid.UUID, completed: bool) -> OnboardingStatusRead:
        """Return current user onboarding progress."""
        session = await self._onboarding.active_session(user_id)
        if session is None:
            return OnboardingStatusRead(
                completed=completed,
                active_session_id=None,
                answered_count=0,
                question_count=0,
            )
        answered, total = await self._onboarding.counts(session)
        return OnboardingStatusRead(
            completed=completed,
            active_session_id=session.id,
            answered_count=answered,
            question_count=total,
        )

    async def get_completion_inputs(
        self, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> tuple[OnboardingSession, list[dict[str, object]]]:
        """Load validated, complete answer inputs before external interpretation."""
        session = await self._owned(user_id, session_id)
        answered, total = await self._onboarding.counts(session)
        if total != 8 or answered != total:
            raise ImportValidationError("All 8 onboarding questions must be answered")
        answers = await self._onboarding.answers(session.id)
        return session, [
            {"question_id": str(item.question_id), "answer": item.answer} for item in answers
        ]

    async def _owned(
        self, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> OnboardingSession:
        session = await self._onboarding.get_session(session_id)
        if session is None or session.user_id != user_id:
            raise OnboardingSessionNotFoundError()
        return session

    async def _session_read(self, session: OnboardingSession) -> OnboardingSessionRead:
        answered, total = await self._onboarding.counts(session)
        return OnboardingSessionRead(
            id=session.id,
            question_set_id=session.question_set_id,
            status=session.status,
            answered_count=answered,
            question_count=total,
        )


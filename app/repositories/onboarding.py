"""Versioned onboarding persistence."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    OnboardingAnswer,
    OnboardingQuestion,
    OnboardingQuestionSet,
    OnboardingSession,
)


class OnboardingRepository:
    """Persist questionnaire sessions and answers."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active_question_set(self, version: int | None = None) -> OnboardingQuestionSet | None:
        """Return the requested or latest active question-set version."""
        query = select(OnboardingQuestionSet).where(OnboardingQuestionSet.is_active)
        if version:
            query = query.where(OnboardingQuestionSet.version == version)
        return await self._session.scalar(
            query.order_by(OnboardingQuestionSet.version.desc()).limit(1)
        )

    async def create_session(
        self, user_id: uuid.UUID, question_set_id: uuid.UUID
    ) -> OnboardingSession:
        """Create an in-progress onboarding session."""
        session = OnboardingSession(user_id=user_id, question_set_id=question_set_id)
        self._session.add(session)
        await self._session.flush()
        return session

    async def get_session(self, session_id: uuid.UUID) -> OnboardingSession | None:
        """Return an onboarding session."""
        return await self._session.get(OnboardingSession, session_id)

    async def active_session(self, user_id: uuid.UUID) -> OnboardingSession | None:
        """Return the user's latest incomplete session."""
        return await self._session.scalar(
            select(OnboardingSession)
            .where(OnboardingSession.user_id == user_id, OnboardingSession.status == "IN_PROGRESS")
            .order_by(OnboardingSession.created_at.desc())
            .limit(1)
        )

    async def questions(self, question_set_id: uuid.UUID) -> list[OnboardingQuestion]:
        """Return all ordered questionnaire questions."""
        return list(
            (
                await self._session.scalars(
                    select(OnboardingQuestion)
                    .where(OnboardingQuestion.question_set_id == question_set_id)
                    .order_by(OnboardingQuestion.position)
                )
            ).all()
        )

    async def upsert_answer(
        self,
        session_id: uuid.UUID,
        question_id: uuid.UUID,
        answer: dict[str, object],
    ) -> None:
        """Create or replace one answer for resumable onboarding."""
        statement = insert(OnboardingAnswer).values(
            session_id=session_id,
            question_id=question_id,
            answer=answer,
        ).on_conflict_do_update(
            index_elements=["session_id", "question_id"],
            set_={"answer": answer, "interpreted_preferences": None},
        )
        await self._session.execute(statement)

    async def answers(self, session_id: uuid.UUID) -> list[OnboardingAnswer]:
        """Return all submitted answers."""
        return list(
            (
                await self._session.scalars(
                    select(OnboardingAnswer).where(OnboardingAnswer.session_id == session_id)
                )
            ).all()
        )

    async def counts(self, session: OnboardingSession) -> tuple[int, int]:
        """Return answered and total question counts."""
        answered = await self._session.scalar(
            select(func.count())
            .select_from(OnboardingAnswer)
            .where(OnboardingAnswer.session_id == session.id)
        )
        total = await self._session.scalar(
            select(func.count())
            .select_from(OnboardingQuestion)
            .where(OnboardingQuestion.question_set_id == session.question_set_id)
        )
        return int(answered or 0), int(total or 0)

    async def complete(self, session: OnboardingSession) -> None:
        """Mark a fully answered session complete."""
        session.status = "COMPLETED"
        session.completed_at = datetime.now(UTC)
        await self._session.flush()

"""Versioned onboarding persistence."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    OnboardingAnswer,
    OnboardingSession,
    OnboardingSessionQuestion,
)


class OnboardingRepository:
    """Persist questionnaire sessions and answers."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_session(
        self, user_id: uuid.UUID, question_set_id: uuid.UUID | None = None
    ) -> OnboardingSession:
        """Create an adaptive in-progress onboarding session."""
        session = OnboardingSession(
            user_id=user_id,
            question_set_id=question_set_id,
            mode="ADAPTIVE",
            generation_state="IDLE",
        )
        self._session.add(session)
        await self._session.flush()
        return session

    async def get_session(self, session_id: uuid.UUID) -> OnboardingSession | None:
        """Return an onboarding session."""
        return await self._session.get(OnboardingSession, session_id)

    async def active_session(
        self, user_id: uuid.UUID, for_update: bool = False
    ) -> OnboardingSession | None:
        """Return the user's latest incomplete session."""
        query = (
            select(OnboardingSession)
            .where(
                OnboardingSession.user_id == user_id,
                OnboardingSession.status.in_(("IN_PROGRESS", "READY_TO_COMPLETE")),
            )
            .order_by(OnboardingSession.created_at.desc())
            .limit(1)
        )
        if for_update:
            query = query.execution_options(populate_existing=True).with_for_update()
        return await self._session.scalar(query)

    async def latest_skipped_session(self, user_id: uuid.UUID) -> OnboardingSession | None:
        """Return the latest explicit skip for idempotent response reporting."""
        return await self._session.scalar(
            select(OnboardingSession)
            .where(OnboardingSession.user_id == user_id, OnboardingSession.status == "SKIPPED")
            .order_by(OnboardingSession.created_at.desc())
            .limit(1)
        )

    async def session_questions(self, session_id: uuid.UUID) -> list[OnboardingSessionQuestion]:
        """Return generated questions for one adaptive session."""
        return list(
            (
                await self._session.scalars(
                    select(OnboardingSessionQuestion)
                    .where(OnboardingSessionQuestion.session_id == session_id)
                    .order_by(OnboardingSessionQuestion.position)
                )
            ).all()
        )

    async def session_question(
        self, session_id: uuid.UUID, question_id: uuid.UUID
    ) -> OnboardingSessionQuestion | None:
        """Return one generated question owned by the session."""
        return await self._session.scalar(
            select(OnboardingSessionQuestion).where(
                OnboardingSessionQuestion.id == question_id,
                OnboardingSessionQuestion.session_id == session_id,
            )
        )

    async def add_session_question(
        self,
        session_id: uuid.UUID,
        position: int,
        values: dict[str, Any],
    ) -> OnboardingSessionQuestion:
        """Persist one provider-generated question."""
        question = OnboardingSessionQuestion(
            session_id=session_id,
            position=position,
            **values,
        )
        self._session.add(question)
        await self._session.flush()
        return question

    async def next_session_question_position(self, session_id: uuid.UUID) -> int:
        """Return the next one-based generated question position."""
        position = await self._session.scalar(
            select(func.coalesce(func.max(OnboardingSessionQuestion.position), 0)).where(
                OnboardingSessionQuestion.session_id == session_id
            )
        )
        return int(position or 0) + 1

    async def delete_questions_after(self, session_id: uuid.UUID, position: int) -> None:
        """Delete generated questions and answers invalidated by an edited answer."""
        question_ids = select(OnboardingSessionQuestion.id).where(
            OnboardingSessionQuestion.session_id == session_id,
            OnboardingSessionQuestion.position > position,
        )
        await self._session.execute(
            delete(OnboardingAnswer).where(OnboardingAnswer.session_question_id.in_(question_ids))
        )
        await self._session.execute(
            delete(OnboardingSessionQuestion).where(
                OnboardingSessionQuestion.session_id == session_id,
                OnboardingSessionQuestion.position > position,
            )
        )

    async def upsert_answer(
        self,
        session_id: uuid.UUID,
        question_id: uuid.UUID | None,
        answer: dict[str, object],
        session_question_id: uuid.UUID | None = None,
    ) -> None:
        """Create or replace one answer for resumable onboarding."""
        values = {
            "session_id": session_id,
            "question_id": question_id,
            "session_question_id": session_question_id,
            "answer": answer,
        }
        conflict_columns = (
            ["session_id", "session_question_id"]
            if session_question_id is not None
            else ["session_id", "question_id"]
        )
        statement = insert(OnboardingAnswer).values(**values).on_conflict_do_update(
            index_elements=conflict_columns,
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

    async def adaptive_answer_rows(
        self, session_id: uuid.UUID
    ) -> list[tuple[OnboardingSessionQuestion, OnboardingAnswer]]:
        """Return generated questions paired with their saved answers."""
        return list(
            (
                await self._session.execute(
                    select(OnboardingSessionQuestion, OnboardingAnswer)
                    .join(
                        OnboardingAnswer,
                        OnboardingAnswer.session_question_id == OnboardingSessionQuestion.id,
                    )
                    .where(OnboardingSessionQuestion.session_id == session_id)
                    .order_by(OnboardingSessionQuestion.position)
                )
            ).all()
        )

    async def counts(self, session: OnboardingSession) -> tuple[int, int]:
        """Return answered and total question counts."""
        answered = await self._session.scalar(
            select(func.count())
            .select_from(OnboardingAnswer)
            .where(
                OnboardingAnswer.session_id == session.id,
                (
                    OnboardingAnswer.session_question_id.is_not(None)
                    if session.mode == "ADAPTIVE"
                    else OnboardingAnswer.question_id.is_not(None)
                ),
            )
        )
        total_query = select(func.count()).select_from(OnboardingSessionQuestion).where(
            OnboardingSessionQuestion.session_id == session.id
        )
        total = await self._session.scalar(total_query)
        return int(answered or 0), int(total or 0)

    async def complete(self, session: OnboardingSession) -> None:
        """Mark a fully answered session complete."""
        session.status = "COMPLETED"
        session.completed_at = datetime.now(UTC)
        await self._session.flush()

"""AI interpretation workflow for onboarding completion."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.logging import get_logger
from app.enums import PreferenceSource
from app.exceptions import ImportValidationError, UserNotFoundError
from app.integrations.llm.base import StructuredTextInterpreter
from app.repositories.catalog import AttributeRepository
from app.repositories.onboarding import OnboardingRepository
from app.repositories.preferences import PreferenceRepository
from app.repositories.users import UserRepository
from app.services.onboarding import OnboardingService


class OnboardingOrchestrator:
    """Keep external interpretation outside database transactions."""

    def __init__(
        self,
        session: AsyncSession,
        interpreter: StructuredTextInterpreter,
    ) -> None:
        self._session = session
        self._interpreter = interpreter

    async def complete(self, user_id: uuid.UUID, session_id: uuid.UUID) -> int:
        """Interpret answers, persist preferences, and complete onboarding atomically."""
        service = OnboardingService(self._session)
        onboarding_session, answers = await service.get_completion_inputs(
            user_id, session_id
        )
        persisted_session_id = onboarding_session.id
        attributes = await AttributeRepository(self._session).list_attributes()
        by_slug = {str(row["slug"]): row for row in attributes}
        await self._session.rollback()
        outputs = await self._interpreter.interpret_onboarding(answers, list(by_slug))
        if not outputs:
            raise ImportValidationError("Onboarding answers produced no recognized preferences")
        preferences = PreferenceRepository(self._session)
        created = 0
        for output in outputs:
            attribute = by_slug.get(output.attribute_slug)
            if attribute is None:
                continue
            await preferences.upsert(
                user_id,
                attribute["id"],
                output.weight,
                output.preferred_value,
                PreferenceSource.ONBOARDING_AI,
            )
            created += 1
        if created == 0:
            raise ImportValidationError(
                "Onboarding answers produced no recognized preferences"
            )
        fresh_session = await OnboardingRepository(self._session).get_session(
            persisted_session_id
        )
        if fresh_session is None:
            raise ImportValidationError("Onboarding session disappeared")
        user = await UserRepository(self._session).get_by_id(user_id)
        if user is None:
            raise UserNotFoundError()
        await OnboardingRepository(self._session).complete(fresh_session)
        user.onboarding_completed = True
        await self._session.commit()
        get_logger().info(
            "onboarding_completed", user_id=str(user_id), preferences=created
        )
        return created

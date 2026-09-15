"""Preference precedence and mutation services."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.logging import get_logger
from app.dto.preferences import PreferenceRead, PreferenceWrite, PreferencesReplace
from app.enums import PreferenceSource
from app.exceptions import PreferenceNotFoundError
from app.repositories.catalog import AttributeRepository
from app.repositories.preferences import PreferenceRepository


class PreferenceService:
    """Own manual and onboarding preference precedence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._preferences = PreferenceRepository(session)
        self._attributes = AttributeRepository(session)

    async def list_effective(self, user_id: uuid.UUID) -> list[PreferenceRead]:
        """Return MANUAL > ONBOARDING effective preferences."""
        return [
            PreferenceRead.model_validate(item)
            for item in await self._preferences.list_effective(user_id)
        ]

    async def replace_manual(
        self, user_id: uuid.UUID, payload: PreferencesReplace
    ) -> list[PreferenceRead]:
        """Replace manual preferences without deleting onboarding preferences."""
        for item in payload.preferences:
            if not await self._attribute_exists(item.attribute_id):
                raise PreferenceNotFoundError("Attribute not found")
        await self._preferences.replace_manual(
            user_id, [item.model_dump() for item in payload.preferences]
        )
        await self._session.commit()
        get_logger().info("preferences_updated", user_id=str(user_id))
        return await self.list_effective(user_id)

    async def upsert_manual(
        self, user_id: uuid.UUID, attribute_id: uuid.UUID, payload: PreferenceWrite
    ) -> list[PreferenceRead]:
        """Upsert one manual preference with path/payload consistency."""
        if payload.attribute_id != attribute_id:
            raise PreferenceNotFoundError("Path and payload attribute IDs differ")
        if not await self._attribute_exists(attribute_id):
            raise PreferenceNotFoundError("Attribute not found")
        await self._preferences.upsert(
            user_id,
            attribute_id,
            payload.importance_weight,
            payload.preferred_value,
            PreferenceSource.USER_MANUAL,
        )
        await self._session.commit()
        return await self.list_effective(user_id)

    async def delete_manual(self, user_id: uuid.UUID, attribute_id: uuid.UUID) -> None:
        """Delete a manual override only."""
        if not await self._preferences.delete_manual(user_id, attribute_id):
            raise PreferenceNotFoundError()
        await self._session.commit()

    async def _attribute_exists(self, attribute_id: uuid.UUID) -> bool:
        rows = await self._attributes.list_attributes()
        return any(row["id"] == attribute_id for row in rows)


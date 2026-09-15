"""User and trip preference persistence."""

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import PreferenceSource
from app.models import Attribute, TripAttributePreference, UserPreference


class PreferenceRepository:
    """Persist preference sources and resolve deterministic precedence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_effective(self, user_id: uuid.UUID) -> list[dict[str, Any]]:
        """Resolve manual preference over onboarding preference per attribute."""
        rows = (
            await self._session.execute(
                select(UserPreference, Attribute)
                .join(Attribute, Attribute.id == UserPreference.attribute_id)
                .where(UserPreference.user_id == user_id, Attribute.is_active)
                .order_by(Attribute.display_order)
            )
        ).all()
        selected: dict[uuid.UUID, tuple[UserPreference, Attribute]] = {}
        for preference, attribute in rows:
            current = selected.get(preference.attribute_id)
            if (
                current is None
                or preference.preference_source == PreferenceSource.USER_MANUAL.value
            ):
                selected[preference.attribute_id] = (preference, attribute)
        return [
            {
                "id": preference.id,
                "attribute_id": attribute.id,
                "attribute_name": attribute.name,
                "attribute_slug": attribute.slug,
                "importance_weight": preference.importance_weight,
                "preferred_value": preference.preferred_value,
                "preference_source": preference.preference_source,
            }
            for preference, attribute in selected.values()
        ]

    async def upsert(
        self,
        user_id: uuid.UUID,
        attribute_id: uuid.UUID,
        importance_weight: float,
        preferred_value: str | None,
        source: PreferenceSource,
    ) -> None:
        """Idempotently persist one preference source."""
        statement = insert(UserPreference).values(
            user_id=user_id,
            attribute_id=attribute_id,
            importance_weight=importance_weight,
            preferred_value=preferred_value,
            preference_source=source.value,
        ).on_conflict_do_update(
            index_elements=["user_id", "attribute_id", "preference_source"],
            set_={
                "importance_weight": importance_weight,
                "preferred_value": preferred_value,
            },
        )
        await self._session.execute(statement)

    async def replace_manual(self, user_id: uuid.UUID, values: list[dict[str, Any]]) -> None:
        """Replace only manual preferences, retaining onboarding output."""
        await self._session.execute(
            delete(UserPreference).where(
                UserPreference.user_id == user_id,
                UserPreference.preference_source == PreferenceSource.USER_MANUAL.value,
            )
        )
        for value in values:
            await self.upsert(user_id=user_id, source=PreferenceSource.USER_MANUAL, **value)

    async def delete_manual(self, user_id: uuid.UUID, attribute_id: uuid.UUID) -> bool:
        """Delete a manual override and reveal onboarding preference if present."""
        result = await self._session.execute(
            delete(UserPreference).where(
                UserPreference.user_id == user_id,
                UserPreference.attribute_id == attribute_id,
                UserPreference.preference_source == PreferenceSource.USER_MANUAL.value,
            )
        )
        return bool(result.rowcount)

    async def trip_preferences(self, trip_id: uuid.UUID) -> list[TripAttributePreference]:
        """Return trip overrides."""
        return list(
            (
                await self._session.scalars(
                    select(TripAttributePreference).where(
                        TripAttributePreference.trip_id == trip_id
                    )
                )
            ).all()
        )

    async def replace_trip(
        self, trip_id: uuid.UUID, preferences: list[dict[str, Any]]
    ) -> list[TripAttributePreference]:
        """Atomically replace all trip-specific preferences."""
        await self._session.execute(
            delete(TripAttributePreference).where(TripAttributePreference.trip_id == trip_id)
        )
        models = [TripAttributePreference(trip_id=trip_id, **value) for value in preferences]
        self._session.add_all(models)
        await self._session.flush()
        return models

    async def resolve_for_trip(
        self, user_id: uuid.UUID, trip_id: uuid.UUID
    ) -> list[dict[str, Any]]:
        """Apply TRIP > MANUAL > ONBOARDING precedence."""
        effective = {item["attribute_id"]: item for item in await self.list_effective(user_id)}
        trip_rows = await self.trip_preferences(trip_id)
        for row in trip_rows:
            base = effective.get(row.attribute_id, {})
            effective[row.attribute_id] = {
                **base,
                "attribute_id": row.attribute_id,
                "importance_weight": row.weight,
                "minimum_required_score": row.minimum_required_score,
                "is_mandatory": row.is_mandatory,
                "preference_source": "TRIP_SPECIFIC",
            }
        return list(effective.values())

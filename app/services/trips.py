"""Trip and trip-specific preference business logic."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.logging import get_logger
from app.dto.trips import TripPreferenceRead, TripPreferencesReplace, TripRead, TripWrite
from app.exceptions import InvalidDestinationError, TripNotFoundError
from app.models import Trip
from app.repositories.locations import LocationRepository
from app.repositories.preferences import PreferenceRepository
from app.repositories.trips import TripRepository


class TripService:
    """Own destination validation and user ownership for trips."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._trips = TripRepository(session)
        self._locations = LocationRepository(session)
        self._preferences = PreferenceRepository(session)

    async def create(self, user_id: uuid.UUID, payload: TripWrite) -> TripRead:
        """Validate hierarchy and create a user-owned trip."""
        await self._validate_destination(payload)
        trip = Trip(user_id=user_id, **payload.model_dump(mode="python"))
        await self._trips.add(trip)
        await self._session.commit()
        get_logger().info("trip_created", user_id=str(user_id), trip_id=str(trip.id))
        return TripRead.model_validate(trip)

    async def list(self, user_id: uuid.UUID) -> list[TripRead]:
        """List owned trips."""
        return [TripRead.model_validate(item) for item in await self._trips.list_owned(user_id)]

    async def get(self, user_id: uuid.UUID, trip_id: uuid.UUID) -> TripRead:
        """Return an owned trip."""
        trip = await self._owned(user_id, trip_id)
        return TripRead.model_validate(trip)

    async def update(
        self, user_id: uuid.UUID, trip_id: uuid.UUID, payload: TripWrite
    ) -> TripRead:
        """Replace mutable trip context after hierarchy validation."""
        await self._validate_destination(payload)
        trip = await self._owned(user_id, trip_id)
        for field, value in payload.model_dump(mode="python").items():
            setattr(trip, field, value)
        await self._session.commit()
        return TripRead.model_validate(trip)

    async def delete(self, user_id: uuid.UUID, trip_id: uuid.UUID) -> None:
        """Soft-delete a user-owned trip."""
        trip = await self._owned(user_id, trip_id)
        trip.deleted_at = datetime.now(UTC)
        await self._session.commit()

    async def preferences(
        self, user_id: uuid.UUID, trip_id: uuid.UUID
    ) -> list[TripPreferenceRead]:
        """Return trip overrides after ownership validation."""
        await self._owned(user_id, trip_id)
        return [
            TripPreferenceRead.model_validate(item)
            for item in await self._preferences.trip_preferences(trip_id)
        ]

    async def replace_preferences(
        self,
        user_id: uuid.UUID,
        trip_id: uuid.UUID,
        payload: TripPreferencesReplace,
    ) -> list[TripPreferenceRead]:
        """Atomically replace trip overrides without changing permanent preferences."""
        await self._owned(user_id, trip_id)
        models = await self._preferences.replace_trip(
            trip_id, [item.model_dump() for item in payload.preferences]
        )
        await self._session.commit()
        return [TripPreferenceRead.model_validate(item) for item in models]

    async def _owned(self, user_id: uuid.UUID, trip_id: uuid.UUID) -> Trip:
        trip = await self._trips.get_owned(trip_id, user_id)
        if trip is None:
            raise TripNotFoundError()
        return trip

    async def _validate_destination(self, payload: TripWrite) -> None:
        if not await self._locations.validate_hierarchy(
            payload.destination_country_id,
            payload.destination_region_id,
            payload.destination_city_id,
        ):
            raise InvalidDestinationError()

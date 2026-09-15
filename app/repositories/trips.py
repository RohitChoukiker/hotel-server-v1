"""User-owned trip persistence."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Trip


class TripRepository:
    """Persist and query trips with ownership filters."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, trip: Trip) -> Trip:
        """Stage a new trip."""
        self._session.add(trip)
        await self._session.flush()
        return trip

    async def get_owned(self, trip_id: uuid.UUID, user_id: uuid.UUID) -> Trip | None:
        """Return a live trip only when owned by the user."""
        return await self._session.scalar(
            select(Trip).where(
                Trip.id == trip_id,
                Trip.user_id == user_id,
                Trip.deleted_at.is_(None),
            )
        )

    async def list_owned(self, user_id: uuid.UUID) -> list[Trip]:
        """List a user's non-deleted trips."""
        return list(
            (
                await self._session.scalars(
                    select(Trip)
                    .where(Trip.user_id == user_id, Trip.deleted_at.is_(None))
                    .order_by(Trip.created_at.desc())
                )
            ).all()
        )


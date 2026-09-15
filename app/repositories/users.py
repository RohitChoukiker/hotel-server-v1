"""Account and refresh-token persistence."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken, User


class UserRepository:
    """Persist and query user accounts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Return a non-deleted user by ID."""
        return await self._session.scalar(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )

    async def get_by_email(self, email: str) -> User | None:
        """Return a non-deleted user by normalized email."""
        return await self._session.scalar(
            select(User).where(User.email == email.lower(), User.deleted_at.is_(None))
        )

    async def get_by_phone(self, phone: str) -> User | None:
        """Return a non-deleted user by exact normalized phone number."""
        return await self._session.scalar(
            select(User).where(User.phone == phone, User.deleted_at.is_(None))
        )

    async def add(self, user: User) -> User:
        """Stage a new user."""
        self._session.add(user)
        await self._session.flush()
        return user

    async def list_users(self, limit: int, cursor_id: uuid.UUID | None) -> list[User]:
        """List users using ID keyset pagination."""
        query = select(User).where(User.deleted_at.is_(None)).order_by(User.id).limit(limit + 1)
        if cursor_id:
            query = query.where(User.id > cursor_id)
        return list((await self._session.scalars(query)).all())

    async def update_role(self, user_id: uuid.UUID, role: str) -> User | None:
        """Atomically update a user role."""
        await self._session.execute(update(User).where(User.id == user_id).values(role=role))
        return await self.get_by_id(user_id)

    async def update_status(self, user_id: uuid.UUID, status: str) -> User | None:
        """Atomically update account status."""
        await self._session.execute(update(User).where(User.id == user_id).values(status=status))
        return await self.get_by_id(user_id)


class RefreshTokenRepository:
    """Persist rotating refresh-token records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: RefreshToken) -> RefreshToken:
        """Stage a refresh token."""
        self._session.add(token)
        await self._session.flush()
        return token

    async def get_by_hash(self, token_hash: str, lock: bool = False) -> RefreshToken | None:
        """Find a refresh token, optionally locking it for rotation."""
        query = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        if lock:
            query = query.with_for_update()
        return await self._session.scalar(query)

    async def revoke_family(self, family_id: uuid.UUID) -> None:
        """Revoke every currently active token in a compromised family."""
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )

"""User profile and administrator account services."""

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dto.users import UserProfile, UserUpdate
from app.enums import UserRole, UserStatus
from app.exceptions import ForbiddenError, PhoneAlreadyExistsError, UserNotFoundError
from app.repositories.operations import AuditRepository
from app.repositories.users import UserRepository


class UserService:
    """Own self-profile and privileged account transitions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._audit = AuditRepository(session)

    async def get(self, user_id: uuid.UUID) -> UserProfile:
        """Return a safe user projection."""
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError()
        return UserProfile.model_validate(user)

    async def update(self, user_id: uuid.UUID, payload: UserUpdate) -> UserProfile:
        """Update only mutable self-profile fields."""
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError()
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(user, field, value)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise PhoneAlreadyExistsError() from exc
        return UserProfile.model_validate(user)

    async def change_role(
        self, actor_id: uuid.UUID, user_id: uuid.UUID, role: UserRole
    ) -> UserProfile:
        """Change role while preventing accidental self-demotion."""
        if actor_id == user_id and role is not UserRole.ADMIN:
            raise ForbiddenError("Administrators cannot demote their own active session")
        user = await self._users.update_role(user_id, role.value)
        if user is None:
            raise UserNotFoundError()
        await self._audit.record(
            actor_id,
            "role_updated",
            "user",
            str(user_id),
            {"role": role.value},
        )
        await self._session.commit()
        return UserProfile.model_validate(user)

    async def change_status(
        self, actor_id: uuid.UUID, user_id: uuid.UUID, status: UserStatus
    ) -> UserProfile:
        """Change account status while protecting the acting administrator."""
        if actor_id == user_id and status is not UserStatus.ACTIVE:
            raise ForbiddenError("Administrators cannot suspend their own active session")
        user = await self._users.update_status(user_id, status.value)
        if user is None:
            raise UserNotFoundError()
        await self._audit.record(
            actor_id,
            "user_status_updated",
            "user",
            str(user_id),
            {"status": status.value},
        )
        await self._session.commit()
        return UserProfile.model_validate(user)

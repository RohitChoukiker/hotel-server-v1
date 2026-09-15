"""User profile and administrator DTOs."""

import uuid
from datetime import datetime

from pydantic import EmailStr, Field, model_validator

from app.dto.common import DTO
from app.enums import UserRole, UserStatus


class UserProfile(DTO):
    """Publicly safe self-profile."""

    id: uuid.UUID
    email: EmailStr
    phone: str | None
    first_name: str
    last_name: str
    status: UserStatus
    role: UserRole
    onboarding_completed: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None


class UserUpdate(DTO):
    """Mutable self-profile fields."""

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, min_length=7, max_length=32)

    @model_validator(mode="after")
    def reject_null_names(self) -> "UserUpdate":
        """Allow omitted names but reject explicitly null required columns."""
        for field in ("first_name", "last_name"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class UserRoleUpdate(DTO):
    """Administrator role transition."""

    role: UserRole


class UserStatusUpdate(DTO):
    """Administrator account-status transition."""

    status: UserStatus

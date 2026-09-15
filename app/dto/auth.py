"""Authentication boundary DTOs."""

import uuid
from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.dto.common import DTO
from app.enums import UserRole, UserStatus


class RegisterRequest(DTO):
    """New-account registration request."""

    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, min_length=7, max_length=32)

    @field_validator("password")
    @classmethod
    def validate_password_complexity(cls, value: str) -> str:
        """Require basic complexity without imposing composition folklore."""
        classes = [
            any(character.islower() for character in value),
            any(character.isupper() for character in value),
            any(character.isdigit() for character in value),
        ]
        if sum(classes) < 2:
            raise ValueError("Password must contain at least two character classes")
        return value


class LoginRequest(DTO):
    """Email/password login request."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(DTO):
    """Refresh-token rotation request."""

    refresh_token: str = Field(min_length=20)


class LogoutRequest(DTO):
    """Refresh-token revocation request."""

    refresh_token: str = Field(min_length=20)


class TokenPair(DTO):
    """Signed token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthUser(DTO):
    """Safe authenticated-user projection."""

    id: uuid.UUID
    email: EmailStr
    phone: str | None
    first_name: str
    last_name: str
    status: UserStatus
    role: UserRole
    onboarding_completed: bool
    created_at: datetime
    last_login_at: datetime | None


class AuthResult(DTO):
    """Authenticated user and token pair."""

    user: AuthUser
    tokens: TokenPair

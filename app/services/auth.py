"""Authentication, JWT rotation, reuse detection, and revocation."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.logging import get_logger
from app.common.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token_identifier,
    verify_password,
)
from app.config import Settings
from app.dto.auth import AuthUser, LoginRequest, RegisterRequest, TokenPair
from app.enums import UserRole, UserStatus
from app.exceptions import (
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    InvalidTokenError,
    PhoneAlreadyExistsError,
    RefreshTokenReuseError,
)
from app.models import RefreshToken, User
from app.repositories.users import RefreshTokenRepository, UserRepository


class AuthService:
    """Own account authentication business rules."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._users = UserRepository(session)
        self._tokens = RefreshTokenRepository(session)

    async def register(
        self,
        request: RegisterRequest,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[AuthUser, TokenPair]:
        """Register a user and issue the first token family atomically."""
        email = str(request.email).lower()
        if await self._users.get_by_email(email):
            raise EmailAlreadyExistsError()
        if request.phone and await self._users.get_by_phone(request.phone):
            raise PhoneAlreadyExistsError()
        user = User(
            email=email,
            phone=request.phone,
            first_name=request.first_name,
            last_name=request.last_name,
            password_hash=hash_password(request.password),
            status=UserStatus.ACTIVE.value,
            role=UserRole.USER.value,
            onboarding_completed=False,
        )
        try:
            await self._users.add(user)
            tokens = await self._issue_pair(user, uuid.uuid4(), user_agent, ip_address)
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if request.phone and await self._users.get_by_phone(request.phone):
                raise PhoneAlreadyExistsError() from exc
            raise EmailAlreadyExistsError() from exc
        get_logger().info("user_registered", user_id=str(user.id))
        return AuthUser.model_validate(user), tokens

    async def login(
        self,
        request: LoginRequest,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[AuthUser, TokenPair]:
        """Authenticate an active user and start a fresh refresh family."""
        user = await self._users.get_by_email(str(request.email).lower())
        if (
            user is None
            or user.status != UserStatus.ACTIVE.value
            or not verify_password(request.password, user.password_hash)
        ):
            raise InvalidCredentialsError()
        user.last_login_at = datetime.now(UTC)
        tokens = await self._issue_pair(user, uuid.uuid4(), user_agent, ip_address)
        await self._session.commit()
        return AuthUser.model_validate(user), tokens

    async def refresh(
        self,
        token: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> TokenPair:
        """Rotate a refresh token; revoke its family when reuse is detected."""
        payload = decode_token(token, "refresh", self._settings.jwt)
        token_hash = hash_token_identifier(str(payload["jti"]))
        stored = await self._tokens.get_by_hash(token_hash, lock=True)
        family_id = uuid.UUID(str(payload["family"]))
        if stored is None or stored.family_id != family_id:
            raise InvalidTokenError()
        if stored.revoked_at is not None or stored.replaced_by_id is not None:
            await self._tokens.revoke_family(stored.family_id)
            await self._session.commit()
            get_logger().warning("refresh_token_reuse_detected", user_id=str(stored.user_id))
            raise RefreshTokenReuseError()
        if stored.expires_at <= datetime.now(UTC):
            raise InvalidTokenError()
        user = await self._users.get_by_id(stored.user_id)
        if user is None or user.status != UserStatus.ACTIVE.value:
            raise InvalidTokenError()
        new_pair, new_model = await self._build_pair(user, family_id, user_agent, ip_address)
        stored.revoked_at = datetime.now(UTC)
        stored.replaced_by_id = new_model.id
        await self._session.commit()
        return new_pair

    async def logout(self, token: str) -> None:
        """Revoke the presented refresh token."""
        payload = decode_token(token, "refresh", self._settings.jwt)
        stored = await self._tokens.get_by_hash(
            hash_token_identifier(str(payload["jti"])), lock=True
        )
        if stored and stored.revoked_at is None:
            stored.revoked_at = datetime.now(UTC)
            await self._session.commit()

    async def _issue_pair(
        self,
        user: User,
        family_id: uuid.UUID,
        user_agent: str | None,
        ip_address: str | None,
    ) -> TokenPair:
        pair, _ = await self._build_pair(user, family_id, user_agent, ip_address)
        return pair

    async def _build_pair(
        self,
        user: User,
        family_id: uuid.UUID,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[TokenPair, RefreshToken]:
        access = create_access_token(user.id, user.role, self._settings.jwt)
        refresh, jti, expires_at = create_refresh_token(user.id, family_id, self._settings.jwt)
        model = RefreshToken(
            user_id=user.id,
            family_id=family_id,
            token_hash=hash_token_identifier(jti),
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        await self._tokens.add(model)
        return (
            TokenPair(
                access_token=access,
                refresh_token=refresh,
                expires_in=self._settings.jwt.access_token_minutes * 60,
            ),
            model,
        )

"""Password hashing, JWT, and opaque-token helpers."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import JWTConfig
from app.exceptions import InvalidTokenError

_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def hash_password(password: str) -> str:
    """Hash a password with Argon2id."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password without leaking hash errors."""
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def hash_token_identifier(identifier: str) -> str:
    """Hash a refresh token identifier before persistence."""
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()


def create_access_token(user_id: uuid.UUID, role: str, config: JWTConfig) -> str:
    """Create a short-lived signed access token."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "jti": secrets.token_urlsafe(24),
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=config.access_token_minutes),
        "iss": config.issuer,
        "aud": config.audience,
    }
    return jwt.encode(payload, config.secret_key.get_secret_value(), algorithm=config.algorithm)


def create_refresh_token(
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    config: JWTConfig,
) -> tuple[str, str, datetime]:
    """Create a refresh JWT and return its raw identifier and expiry."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=config.refresh_token_days)
    jti = secrets.token_urlsafe(32)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": jti,
        "family": str(family_id),
        "iat": now,
        "nbf": now,
        "exp": expires_at,
        "iss": config.issuer,
        "aud": config.audience,
    }
    token = jwt.encode(payload, config.secret_key.get_secret_value(), algorithm=config.algorithm)
    return token, jti, expires_at


def decode_token(token: str, expected_type: str, config: JWTConfig) -> dict[str, Any]:
    """Decode and validate a JWT including its intended token type."""
    try:
        payload = jwt.decode(
            token,
            config.secret_key.get_secret_value(),
            algorithms=[config.algorithm],
            audience=config.audience,
            issuer=config.issuer,
            options={"require": ["exp", "iat", "sub", "jti", "type"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError() from exc
    if payload.get("type") != expected_type:
        raise InvalidTokenError("Unexpected token type")
    return payload


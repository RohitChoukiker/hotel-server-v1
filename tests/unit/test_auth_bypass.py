"""Development-only authentication bypass tests."""

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import register_exception_handlers
from app.config import (
    AppConfig,
    DatabaseConfig,
    JWTConfig,
    LLMConfig,
    RedisConfig,
    Settings,
)
from app.dependencies import CurrentUserDep, database_session, settings_dependency
from app.enums import Environment, UserRole, UserStatus
from app.models import User
from app.repositories.users import UserRepository

DEV_USER_EMAIL = "dev-user@localhost.test"


def _settings(environment: Environment, bypass_enabled: bool) -> Settings:
    """Build settings without relying on the process environment."""
    return Settings(
        app=AppConfig(environment=environment),
        auth_bypass_enabled=bypass_enabled,
        auth_bypass_user_email=DEV_USER_EMAIL,
        database=DatabaseConfig(url="postgresql+asyncpg://hotel:strong@db.internal:5432/hotel"),
        redis=RedisConfig(url="redis://:strong@redis:6379/0", fail_open=False),
        jwt=JWTConfig(secret_key="x" * 48),
        llm=LLMConfig(provider="deterministic"),
    )


def _client(settings: Settings, monkeypatch) -> tuple[TestClient, User]:
    """Create a protected test endpoint backed by the user repository dependency."""
    user = User(
        id=uuid.uuid4(),
        email=DEV_USER_EMAIL,
        first_name="Development",
        last_name="User",
        password_hash="unused",
        status=UserStatus.ACTIVE.value,
        role=UserRole.USER.value,
    )

    async def get_by_email(self: UserRepository, email: str) -> User | None:
        return user if email == DEV_USER_EMAIL else None

    monkeypatch.setattr(UserRepository, "get_by_email", get_by_email)

    app = FastAPI()

    async def fake_database_session():
        yield object()

    app.dependency_overrides[database_session] = fake_database_session
    app.dependency_overrides[settings_dependency] = lambda: settings

    @app.get("/protected")
    async def protected(current_user: CurrentUserDep) -> dict[str, str]:
        return {"user_id": str(current_user.id)}

    register_exception_handlers(app, settings)
    return TestClient(app), user


def test_development_bypass_allows_protected_endpoint_without_token(monkeypatch) -> None:
    """Development bypass authenticates as the active database-backed USER account."""
    settings = _settings(Environment.DEVELOPMENT, bypass_enabled=True)
    client, user = _client(settings, monkeypatch)

    response = client.get("/protected")

    assert response.status_code == 200
    assert response.json() == {"user_id": str(user.id)}


def test_development_bypass_disabled_requires_token(monkeypatch) -> None:
    """The default development behavior still requires JWT authentication."""
    settings = _settings(Environment.DEVELOPMENT, bypass_enabled=False)
    client, _ = _client(settings, monkeypatch)

    response = client.get("/protected")

    assert response.status_code == 401


def test_production_bypass_flag_still_requires_token(monkeypatch) -> None:
    """Production never activates the development-only bypass."""
    settings = _settings(Environment.PRODUCTION, bypass_enabled=True)
    client, _ = _client(settings, monkeypatch)

    response = client.get("/protected")

    assert response.status_code == 401

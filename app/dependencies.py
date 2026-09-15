"""FastAPI dependency injection for sessions, auth, clients, and policies."""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

import structlog.contextvars
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.common.cache import Cache
from app.common.rate_limit import RateLimiter
from app.common.security import decode_token
from app.config import Settings, get_settings
from app.enums import UserRole, UserStatus
from app.exceptions import ForbiddenError, UnauthorizedError
from app.integrations.llm.base import StructuredTextInterpreter
from app.integrations.storage.base import ObjectStorage
from app.integrations.tasks import TaskDispatcher
from app.models import User
from app.repositories.users import UserRepository

bearer = HTTPBearer(auto_error=False)


def settings_dependency() -> Settings:
    """Inject validated settings."""
    return get_settings()


async def database_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Inject a request-scoped async session with rollback safety."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def redis_dependency(request: Request) -> Redis:
    """Inject the process Redis client."""
    return request.app.state.redis


def cache_dependency(request: Request) -> Cache:
    """Inject the namespaced cache adapter."""
    return request.app.state.cache


def interpreter_dependency(request: Request) -> StructuredTextInterpreter:
    """Inject a structured text interpreter."""
    return request.app.state.text_interpreter


def storage_dependency(request: Request) -> ObjectStorage:
    """Inject immutable import object storage."""
    return request.app.state.object_storage


def task_dispatcher_dependency(request: Request) -> TaskDispatcher:
    """Inject the Celery task dispatcher."""
    return request.app.state.task_dispatcher


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(database_session)],
    settings: Annotated[Settings, Depends(settings_dependency)],
) -> User:
    """Authenticate an active user from a bearer access token."""
    if credentials is None:
        raise UnauthorizedError()
    payload = decode_token(credentials.credentials, "access", settings.jwt)
    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (ValueError, KeyError) as exc:
        raise UnauthorizedError() from exc
    user = await UserRepository(session).get_by_id(user_id)
    if user is None or user.status != UserStatus.ACTIVE.value:
        raise UnauthorizedError()
    request.state.user = user
    structlog.contextvars.bind_contextvars(user_id=str(user.id))
    return user


def require_roles(*roles: UserRole) -> Callable[..., Awaitable[User]]:
    """Build a role dependency that runs after authentication."""

    async def role_guard(user: Annotated[User, Depends(current_user)]) -> User:
        if UserRole(user.role) not in roles:
            raise ForbiddenError()
        return user

    return role_guard


def rate_limit(bucket: str) -> Callable[..., Awaitable[None]]:
    """Build a named Redis rate-limit dependency."""

    async def limiter(
        request: Request,
        settings: Annotated[Settings, Depends(settings_dependency)],
    ) -> None:
        rate_limiter: RateLimiter = request.app.state.rate_limiter
        user_id = getattr(getattr(request.state, "user", None), "id", None)
        principal = str(user_id or (request.client.host if request.client else "unknown"))
        policy = getattr(settings.rate_limits, bucket)
        await rate_limiter.check(bucket, principal, policy)

    return limiter


SessionDep = Annotated[AsyncSession, Depends(database_session)]
SettingsDep = Annotated[Settings, Depends(settings_dependency)]
CurrentUserDep = Annotated[User, Depends(current_user)]
RedisDep = Annotated[Redis, Depends(redis_dependency)]
CacheDep = Annotated[Cache, Depends(cache_dependency)]
InterpreterDep = Annotated[StructuredTextInterpreter, Depends(interpreter_dependency)]
StorageDep = Annotated[ObjectStorage, Depends(storage_dependency)]
TaskDispatcherDep = Annotated[TaskDispatcher, Depends(task_dispatcher_dependency)]

"""FastAPI application wiring."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from redis.asyncio import Redis

from app.api.errors import register_exception_handlers
from app.api.health import router as health_router
from app.api.v1.router import router as v1_router
from app.common.cache import Cache
from app.common.logging import configure_logging
from app.common.middleware import (
    RequestContextMiddleware,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.common.rate_limit import RateLimiter
from app.config import Settings, get_settings
from app.database import create_engine, create_session_factory
from app.integrations.llm.deterministic import DeterministicTextInterpreter
from app.integrations.llm.openai_structured import OpenAIStructuredInterpreter
from app.integrations.storage.gcs import GCSObjectStorage
from app.integrations.storage.local import LocalObjectStorage
from app.integrations.tasks import TaskDispatcher
from app.workers.celery_app import celery_app


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application with explicit lifecycle-managed dependencies."""
    config = settings or get_settings()
    configure_logging(config.logging)
    if config.sentry.dsn:
        sentry_sdk.init(
            dsn=config.sentry.dsn.get_secret_value(),
            environment=config.app.environment.value,
            traces_sample_rate=config.sentry.traces_sample_rate,
            send_default_pii=False,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(config)
        redis = Redis.from_url(config.redis.url.get_secret_value(), decode_responses=True)
        http_client = httpx.AsyncClient()
        app.state.settings = config
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.redis = redis
        app.state.cache = Cache(redis, config.redis.cache_prefix)
        app.state.rate_limiter = RateLimiter(
            redis, config.redis.cache_prefix, config.redis.fail_open
        )
        app.state.task_dispatcher = TaskDispatcher(celery_app)
        if config.storage.backend == "gcs":
            assert config.storage.gcs_bucket is not None
            app.state.object_storage = GCSObjectStorage(config.storage.gcs_bucket)
        else:
            app.state.object_storage = LocalObjectStorage(config.storage.local_path)
        if config.llm.provider == "openai":
            app.state.text_interpreter = OpenAIStructuredInterpreter(
                config.llm, http_client
            )
        else:
            app.state.text_interpreter = DeterministicTextInterpreter()
        try:
            yield
        finally:
            await http_client.aclose()
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(
        title=config.app.app_name,
        version="1.0.0",
        debug=config.app.debug,
        lifespan=lifespan,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.app.trusted_hosts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors.allowed_origins,
        allow_credentials=config.cors.allow_credentials,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Correlation-ID"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=config.app.max_request_bytes)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app, config)
    app.include_router(health_router)
    app.include_router(v1_router, prefix=config.app.api_prefix)
    return app


app = create_app()

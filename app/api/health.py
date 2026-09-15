"""Liveness, readiness, and Prometheus endpoints."""

from fastapi import APIRouter, Request
from fastapi.responses import ORJSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.dependencies import SessionDep

router = APIRouter(tags=["operations"])


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Return process liveness without dependency calls."""
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(request: Request, session: SessionDep) -> ORJSONResponse:
    """Verify PostgreSQL, Redis, and a recent Celery worker heartbeat."""
    checks: dict[str, str] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    try:
        redis = request.app.state.redis
        await redis.ping()
        checks["redis"] = "ok"
        heartbeat_key = f"{request.app.state.settings.redis.cache_prefix}:worker:heartbeat"
        checks["worker"] = "ok" if await redis.exists(heartbeat_key) else "no_heartbeat"
    except Exception:
        checks["redis"] = "unavailable"
        checks["worker"] = "unknown"
    ready = all(value == "ok" for value in checks.values())
    return ORJSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Expose Prometheus text-format metrics."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


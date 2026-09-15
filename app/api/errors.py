"""Centralized domain and framework exception mapping."""

from typing import Any

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse

from app.common.logging import get_logger
from app.config import Settings
from app.exceptions import DomainError


def _body(
    request: Request,
    code: str,
    message: str,
    details: Any | None,
) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "details": details},
        "request_id": getattr(request.state, "request_id", None),
    }


def register_exception_handlers(app: FastAPI, settings: Settings) -> None:
    """Register stable error envelopes without leaking production traces."""

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> ORJSONResponse:
        return ORJSONResponse(
            status_code=exc.status_code,
            content=_body(request, exc.code, exc.public_message, exc.details),
            headers={"Retry-After": str(exc.details.get("retry_after", 60))}
            if exc.status_code == 429 and isinstance(exc.details, dict)
            else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> ORJSONResponse:
        details = [
            {"location": list(item["loc"]), "message": item["msg"], "type": item["type"]}
            for item in exc.errors()
        ]
        return ORJSONResponse(
            status_code=422,
            content=_body(request, "VALIDATION_ERROR", "Request validation failed", details),
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> ORJSONResponse:
        get_logger().exception("unhandled_exception", error_type=type(exc).__name__)
        if settings.sentry.dsn is not None:
            sentry_sdk.capture_exception(exc)
        message = str(exc) if settings.app.debug else "An unexpected error occurred"
        return ORJSONResponse(
            status_code=500,
            content=_body(request, "INTERNAL_ERROR", message, None),
        )

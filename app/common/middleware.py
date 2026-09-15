"""HTTP request context, size, metrics, and security middleware."""

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog.contextvars
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.common.logging import get_logger
from app.common.observability import HTTP_DURATION, HTTP_REQUESTS

RequestHandler = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach correlation identifiers and structured request telemetry."""

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        correlation_id = request.headers.get("X-Correlation-ID") or request_id
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            correlation_id=correlation_id,
        )
        started = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - started
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Correlation-ID"] = correlation_id
        HTTP_REQUESTS.labels(request.method, route_path, str(response.status_code)).inc()
        HTTP_DURATION.labels(request.method, route_path).observe(duration)
        get_logger().info(
            "http_request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration * 1000, 2),
        )
        structlog.contextvars.clear_contextvars()
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject unbounded or oversized request bodies before endpoint handling."""

    def __init__(self, app: object, max_bytes: int) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        content_length = request.headers.get("content-length")
        body_method = request.method in {"POST", "PUT", "PATCH"}
        if body_method and content_length is None:
            return self._error_response(
                request,
                411,
                "CONTENT_LENGTH_REQUIRED",
                "A Content-Length header is required for request bodies",
            )
        try:
            declared_size = int(content_length) if content_length else 0
        except ValueError:
            return self._error_response(
                request,
                400,
                "INVALID_CONTENT_LENGTH",
                "Content-Length must be a non-negative integer",
            )
        if declared_size < 0:
            return self._error_response(
                request,
                400,
                "INVALID_CONTENT_LENGTH",
                "Content-Length must be a non-negative integer",
            )
        if declared_size > self.max_bytes:
            return self._error_response(
                request,
                413,
                "REQUEST_TOO_LARGE",
                "Request body exceeds the configured limit",
            )
        return await call_next(request)

    @staticmethod
    def _error_response(
        request: Request,
        status_code: int,
        code: str,
        message: str,
    ) -> JSONResponse:
        """Build the standard error envelope for transport-level failures."""
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {"code": code, "message": message, "details": None},
                "request_id": getattr(request.state, "request_id", None),
            },
        )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set conservative API security headers."""

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

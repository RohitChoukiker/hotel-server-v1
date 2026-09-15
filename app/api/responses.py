"""Standard response envelope builders."""

from typing import Any, TypeVar

from fastapi import Request

from app.dto.common import ListResponse, Pagination, SuccessResponse

T = TypeVar("T")


def success(request: Request, data: T, meta: dict[str, Any] | None = None) -> SuccessResponse[T]:
    """Wrap a success payload with the current request ID."""
    return SuccessResponse(
        data=data,
        meta=meta or {},
        request_id=request.state.request_id,
    )


def listing(
    request: Request,
    data: list[T],
    pagination: Pagination,
) -> ListResponse[T]:
    """Wrap a list payload with pagination and the request ID."""
    return ListResponse(data=data, pagination=pagination, request_id=request.state.request_id)


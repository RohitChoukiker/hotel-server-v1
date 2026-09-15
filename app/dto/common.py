"""Standard response envelopes and pagination DTOs."""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class DTO(BaseModel):
    """Strict API boundary base model."""

    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=True)


class SuccessResponse(DTO, Generic[T]):
    """Single-resource success envelope."""

    data: T
    meta: dict[str, Any] = Field(default_factory=dict)
    request_id: str


class Pagination(DTO):
    """Cursor-page metadata."""

    next_cursor: str | None = None
    has_more: bool = False
    limit: int


class ListResponse(DTO, Generic[T]):
    """List response envelope."""

    data: list[T]
    pagination: Pagination
    request_id: str


class ErrorBody(DTO):
    """Stable error detail."""

    code: str
    message: str
    details: Any | None = None


class ErrorResponse(DTO):
    """Standard error envelope."""

    error: ErrorBody
    request_id: str | None = None


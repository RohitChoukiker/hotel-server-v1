"""Opaque cursor encoding utilities."""

import base64
import json
import math
import uuid
from datetime import date, datetime
from typing import Any

from app.exceptions import DomainError


class InvalidCursorError(DomainError):
    """Raised for malformed client cursors."""

    code = "INVALID_CURSOR"
    message = "Pagination cursor is invalid"
    status_code = 422


def encode_cursor(values: dict[str, Any]) -> str:
    """Encode JSON-safe keyset values as a URL-safe cursor."""
    normalized = {
        key: value.isoformat() if isinstance(value, datetime) else str(value)
        for key, value in values.items()
    }
    raw = json.dumps(normalized, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str | None) -> dict[str, str]:
    """Decode a cursor or return an empty keyset."""
    if not cursor:
        return {}
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursorError() from exc
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise InvalidCursorError()
    return value


def cursor_uuid(values: dict[str, str], key: str) -> uuid.UUID | None:
    """Parse an optional UUID cursor field into a stable validation error."""
    if key not in values:
        return None
    try:
        return uuid.UUID(values[key])
    except (ValueError, AttributeError) as exc:
        raise InvalidCursorError() from exc


def cursor_date(values: dict[str, str], key: str) -> date | None:
    """Parse an optional ISO date cursor field."""
    if key not in values:
        return None
    try:
        return date.fromisoformat(values[key])
    except ValueError as exc:
        raise InvalidCursorError() from exc


def cursor_datetime(values: dict[str, str], key: str) -> datetime | None:
    """Parse an optional timezone-aware ISO datetime cursor field."""
    if key not in values:
        return None
    try:
        parsed = datetime.fromisoformat(values[key])
    except ValueError as exc:
        raise InvalidCursorError() from exc
    if parsed.tzinfo is None:
        raise InvalidCursorError()
    return parsed


def cursor_float(values: dict[str, str], key: str) -> float | None:
    """Parse an optional finite floating-point cursor field."""
    if key not in values:
        return None
    try:
        parsed = float(values[key])
    except ValueError as exc:
        raise InvalidCursorError() from exc
    if not math.isfinite(parsed):
        raise InvalidCursorError()
    return parsed


def cursor_int(values: dict[str, str], key: str) -> int | None:
    """Parse an optional integer cursor field."""
    if key not in values:
        return None
    try:
        return int(values[key])
    except ValueError as exc:
        raise InvalidCursorError() from exc

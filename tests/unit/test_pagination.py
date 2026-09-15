"""Opaque cursor tests."""

import unittest
import uuid
from datetime import UTC, datetime

from app.common.pagination import (
    InvalidCursorError,
    cursor_datetime,
    cursor_float,
    cursor_uuid,
    decode_cursor,
    encode_cursor,
)


class PaginationTests(unittest.TestCase):
    """Verify cursor round trips and rejection of malformed client values."""

    def test_empty_cursor(self) -> None:
        self.assertEqual(decode_cursor(None), {})

    def test_round_trip(self) -> None:
        identifier = uuid.UUID("12345678-1234-5678-1234-567812345678")
        moment = datetime(2026, 9, 11, tzinfo=UTC)
        decoded = decode_cursor(encode_cursor({"id": identifier, "created": moment}))
        self.assertEqual(decoded, {"id": str(identifier), "created": moment.isoformat()})

    def test_invalid_base64(self) -> None:
        with self.assertRaises(InvalidCursorError):
            decode_cursor("not valid!@#")

    def test_non_object_cursor(self) -> None:
        with self.assertRaises(InvalidCursorError):
            decode_cursor("WzFd")

    def test_invalid_typed_cursor_fields(self) -> None:
        with self.assertRaises(InvalidCursorError):
            cursor_uuid({"id": "not-a-uuid"}, "id")
        with self.assertRaises(InvalidCursorError):
            cursor_float({"score": "nan"}, "score")
        with self.assertRaises(InvalidCursorError):
            cursor_datetime({"created": "2026-09-11T10:00:00"}, "created")


if __name__ == "__main__":
    unittest.main()

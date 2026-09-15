"""CSV parsing and data-quality classification unit tests."""

import unittest
from datetime import date

from app.exceptions import ImportValidationError
from app.orchestrators.imports import CSVImportOrchestrator


class ImportValidationTests(unittest.TestCase):
    """Verify source values are parsed safely without silent coercion."""

    def test_coordinates_are_bounded(self) -> None:
        self.assertEqual(
            CSVImportOrchestrator._coordinate("15.5", -90, 90, "latitude"),
            15.5,
        )
        with self.assertRaises(ImportValidationError):
            CSVImportOrchestrator._coordinate("91", -90, 90, "latitude")

    def test_dates_and_invalid_date(self) -> None:
        self.assertEqual(
            CSVImportOrchestrator._parse_date("11/09/2026"), date(2026, 9, 11)
        )
        with self.assertRaises(ImportValidationError):
            CSVImportOrchestrator._parse_date("not-a-date")

    def test_image_formats_and_utf8(self) -> None:
        self.assertEqual(
            CSVImportOrchestrator._image_urls('["https://x/é.jpg"]'),
            ["https://x/é.jpg"],
        )
        self.assertEqual(
            CSVImportOrchestrator._image_urls("https://x/a.jpg, https://x/b.jpg"),
            ["https://x/a.jpg", "https://x/b.jpg"],
        )

    def test_quality_types(self) -> None:
        error = ImportValidationError("INVALID_REVIEW_RATING")
        self.assertEqual(
            CSVImportOrchestrator._quality_type(error), "INVALID_REVIEW_RATING"
        )

    def test_external_identifiers_are_not_normalized(self) -> None:
        value = " 000123 "
        self.assertEqual(
            CSVImportOrchestrator._required_identifier(
                {"locationId": value}, "locationId"
            ),
            value,
        )


if __name__ == "__main__":
    unittest.main()

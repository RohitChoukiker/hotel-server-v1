"""CSV parsing and data-quality classification unit tests."""

import unittest
from datetime import date
from uuid import uuid4

from app.dto.hotels import HotelListItem
from app.exceptions import ImportValidationError
from app.models import Hotel
from app.orchestrators.imports import HOTEL_COLUMNS, CSVImportOrchestrator


class ImportValidationTests(unittest.TestCase):
    """Verify source values are parsed safely without silent coercion."""

    def test_blank_coordinates_are_optional(self) -> None:
        self.assertIsNone(
            CSVImportOrchestrator._coordinate("", -90, 90, "latitude")
        )
        self.assertIsNone(
            CSVImportOrchestrator._coordinate("   ", -180, 180, "longitude")
        )

    def test_valid_coordinates_are_parsed(self) -> None:
        self.assertEqual(
            CSVImportOrchestrator._coordinate("23.72", -90, 90, "latitude"),
            23.72,
        )

    def test_malformed_coordinates_are_rejected(self) -> None:
        with self.assertRaises(ImportValidationError):
            CSVImportOrchestrator._coordinate("abc", -90, 90, "latitude")

    def test_out_of_range_coordinates_are_rejected(self) -> None:
        with self.assertRaises(ImportValidationError):
            CSVImportOrchestrator._coordinate("999", -90, 90, "latitude")
        with self.assertRaises(ImportValidationError):
            CSVImportOrchestrator._coordinate("999", -180, 180, "longitude")

    def test_coordinate_columns_are_optional(self) -> None:
        CSVImportOrchestrator._require_columns(
            ["locationId", "name", "city"], HOTEL_COLUMNS
        )

    def test_hotel_model_coordinates_are_nullable(self) -> None:
        self.assertTrue(Hotel.__table__.c.latitude.nullable)
        self.assertTrue(Hotel.__table__.c.longitude.nullable)
        self.assertTrue(Hotel.__table__.c.geo_location.nullable)

    def test_hotel_response_allows_missing_coordinates(self) -> None:
        hotel = HotelListItem.model_validate(
            {
                "id": uuid4(),
                "name": "Hotel Without Coordinates",
                "hotel_type": None,
                "city": "Aizawl",
                "region": "Mizoram",
                "country": "India",
                "latitude": None,
                "longitude": None,
            }
        )

        self.assertIsNone(hotel.latitude)
        self.assertIsNone(hotel.longitude)

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

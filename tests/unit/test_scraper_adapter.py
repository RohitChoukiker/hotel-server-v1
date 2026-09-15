"""TripAdvisor request-classification and rate-limit tests."""

import unittest

import httpx

from app.config import ScraperConfig
from app.exceptions import DependencyUnavailableError, SourceRateLimitError
from app.integrations.scraper.tripadvisor import (
    LISTING_QUERY_ID,
    TripAdvisorAdapter,
    is_hotel_listing_payload,
)


class ScraperAdapterTests(unittest.IsolatedAsyncioTestCase):
    """Ensure the source adapter cannot confuse analytics with listings."""

    def test_listing_classifier(self) -> None:
        listing = {
            "preRegisteredQueryId": LISTING_QUERY_ID,
            "variables": {"geoId": "123", "offset": 0, "limit": 30},
        }
        self.assertTrue(is_hotel_listing_payload(listing))
        listing["variables"]["event"] = "hotels_component_seen"
        self.assertFalse(is_hotel_listing_payload(listing))

    async def test_disabled_by_default(self) -> None:
        async with httpx.AsyncClient() as client:
            adapter = TripAdvisorAdapter(ScraperConfig(), client)
            with self.assertRaises(DependencyUnavailableError):
                await adapter.fetch_listing_page("123", 0)

    async def test_retry_after_is_preserved(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "17"}, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = TripAdvisorAdapter(
                ScraperConfig(tripadvisor_enabled=True), client
            )
            with self.assertRaises(SourceRateLimitError) as raised:
                await adapter.fetch_listing_page("123", 0)
        self.assertEqual(raised.exception.retry_after_seconds, 17)


if __name__ == "__main__":
    unittest.main()

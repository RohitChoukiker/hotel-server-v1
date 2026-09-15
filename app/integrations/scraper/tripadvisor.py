"""Guarded TripAdvisor source adapter isolated from canonical domain models."""

from typing import Any

import httpx

from app.config import ScraperConfig
from app.exceptions import DependencyUnavailableError, ImportValidationError, SourceRateLimitError

LISTING_QUERY_ID = "f101de74ce917363"
ANALYTICS_MARKERS = {
    "user_interaction",
    "user_impression",
    "hotels_component_seen",
    "braze_identity_linked",
}


def is_hotel_listing_payload(payload: dict[str, Any]) -> bool:
    """Reject analytics traffic and recognize only the known listing query shape."""
    serialized = str(payload).casefold()
    if any(marker in serialized for marker in ANALYTICS_MARKERS):
        return False
    return (
        payload.get("preRegisteredQueryId") == LISTING_QUERY_ID
        and isinstance(payload.get("variables"), dict)
        and "geoId" in payload["variables"]
        and "offset" in payload["variables"]
        and "limit" in payload["variables"]
    )


class TripAdvisorAdapter:
    """Fetch explicitly configured listing pages without inventing geo identifiers."""

    endpoint = "https://www.tripadvisor.in/data/graphql/ids"

    def __init__(self, config: ScraperConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client

    async def fetch_listing_page(
        self,
        geo_id: str,
        offset: int,
        limit: int = 30,
        extra_variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fetch one explicit page and validate its request classification."""
        if not self._config.tripadvisor_enabled:
            raise DependencyUnavailableError("TripAdvisor scraping is disabled")
        if not geo_id.strip():
            raise ImportValidationError("An explicit TripAdvisor geoId is required")
        variables = {"geoId": geo_id, "offset": offset, "limit": limit, **(extra_variables or {})}
        payload = {"preRegisteredQueryId": LISTING_QUERY_ID, "variables": variables}
        if not is_hotel_listing_payload(payload):
            raise ImportValidationError("Request was not recognized as a hotel listing request")
        headers = {"User-Agent": self._config.user_agent, "Content-Type": "application/json"}
        if self._config.tripadvisor_cookie:
            headers["Cookie"] = self._config.tripadvisor_cookie.get_secret_value()
        try:
            response = await self._client.post(
                self.endpoint,
                json=payload,
                headers=headers,
                timeout=self._config.request_timeout_seconds,
            )
            if response.status_code == 429:
                raw_retry_after = response.headers.get("Retry-After", "60")
                retry_after = int(raw_retry_after) if raw_retry_after.isdigit() else 60
                raise SourceRateLimitError(retry_after)
            response.raise_for_status()
            data = response.json()
        except SourceRateLimitError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise DependencyUnavailableError("TripAdvisor request failed") from exc
        if not isinstance(data, dict):
            raise ImportValidationError("TripAdvisor returned an unexpected response shape")
        return data

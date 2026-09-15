"""Prometheus metrics owned by the HTTP process."""

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "hotel_api_http_requests_total",
    "HTTP requests processed",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "hotel_api_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "route"],
)
RECOMMENDATIONS = Counter(
    "hotel_api_recommendations_total",
    "Recommendation runs",
    ["status"],
)
IMPORT_ROWS = Counter(
    "hotel_api_import_rows_total",
    "CSV import rows processed",
    ["kind", "result"],
)


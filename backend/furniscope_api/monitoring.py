"""Low-cardinality Prometheus instrumentation shared by the API."""

from prometheus_client import REGISTRY, Counter, Histogram


def _existing_or_create(factory, name: str, *args, **kwargs):
    """Tolerate the repository's two supported package import roots in one test process."""
    existing = REGISTRY._names_to_collectors.get(name)  # prometheus_client has no public lookup API.
    return existing if existing is not None else factory(name, *args, **kwargs)


HTTP_REQUESTS = _existing_or_create(
    Counter, "furniscope_http_requests_total", "HTTP requests handled by the API.",
    ("method", "route", "status"),
)
HTTP_DURATION = _existing_or_create(
    Histogram, "furniscope_http_request_duration_seconds", "HTTP request latency in seconds.",
    ("method", "route"), buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)

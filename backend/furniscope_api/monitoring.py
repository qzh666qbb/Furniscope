"""Low-cardinality Prometheus instrumentation shared by the API."""

from prometheus_client import REGISTRY, Counter, Gauge, Histogram


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
DATA_QUERY_TOTAL = _existing_or_create(
    Counter,
    "furniscope_data_queries_total",
    "Controlled semantic data queries by outcome and source kind.",
    ("status", "source_kind"),
)
DATA_QUERY_DURATION = _existing_or_create(
    Histogram,
    "furniscope_data_query_duration_seconds",
    "Controlled semantic data-query execution latency.",
    ("source_kind",),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
KNOWLEDGE_RETRIEVAL_TOTAL = _existing_or_create(
    Counter,
    "furniscope_knowledge_retrievals_total",
    "Knowledge retrieval outcomes after the relevance threshold.",
    ("status",),
)
KNOWLEDGE_RETRIEVAL_MATCHES = _existing_or_create(
    Histogram,
    "furniscope_knowledge_retrieval_matches",
    "Relevant chunks returned by a knowledge retrieval.",
    buckets=(0, 1, 2, 4, 8, 16, 32, 50),
)
WORKER_HEARTBEAT = _existing_or_create(
    Gauge,
    "furniscope_worker_heartbeat_unixtime",
    "Unix time of the latest worker maintenance heartbeat.",
)
WORKER_MAINTENANCE_FAILURES = _existing_or_create(
    Counter,
    "furniscope_worker_maintenance_failures_total",
    "Worker scheduler maintenance failures by operation.",
    ("operation",),
)

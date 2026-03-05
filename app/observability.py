import logging
import time
from contextlib import contextmanager
from typing import Any, Generator

from app.config import settings

try:
    from pythonjsonlogger import jsonlogger
except Exception:  # pragma: no cover
    jsonlogger = None

try:
    import sentry_sdk
except Exception:  # pragma: no cover
    sentry_sdk = None

try:
    from prometheus_client import Counter, Histogram, start_http_server
except Exception:  # pragma: no cover
    Counter = None
    Histogram = None
    start_http_server = None


if Counter is not None:
    RAG_REQUESTS_TOTAL = Counter(
        "rag_requests_total",
        "Total number of RAG requests",
        ["status"],
    )
    RAG_REQUEST_DURATION_SECONDS = Histogram(
        "rag_request_duration_seconds",
        "Duration of RAG requests",
    )
    DOCUMENT_UPLOADS_TOTAL = Counter(
        "document_uploads_total",
        "Total number of document uploads",
        ["status"],
    )
    INDEX_BUILD_DURATION_SECONDS = Histogram(
        "index_build_duration_seconds",
        "Duration of index rebuild operations",
    )
else:  # pragma: no cover
    RAG_REQUESTS_TOTAL = None
    RAG_REQUEST_DURATION_SECONDS = None
    DOCUMENT_UPLOADS_TOTAL = None
    INDEX_BUILD_DURATION_SECONDS = None


def configure_logging() -> None:
    root = logging.getLogger()
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    root.setLevel(level)

    if root.handlers:
        root.handlers.clear()

    handler = logging.StreamHandler()
    if settings.LOG_JSON and jsonlogger is not None:
        formatter = jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        )

    handler.setFormatter(formatter)
    root.addHandler(handler)


def init_error_tracking() -> None:
    if not settings.SENTRY_DSN or sentry_sdk is None:
        return

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        environment=settings.APP_ENV,
    )


def start_metrics_server() -> None:
    if not settings.METRICS_ENABLED or start_http_server is None:
        return
    start_http_server(settings.METRICS_PORT)


@contextmanager
def measure_duration(metric: Any) -> Generator[None, None, None]:
    started_at = time.perf_counter()
    try:
        yield
    finally:
        if metric is not None:
            metric.observe(time.perf_counter() - started_at)


def increment_counter(metric: Any, status: str) -> None:
    if metric is not None:
        metric.labels(status=status).inc()


def audit_event(event: str, user_id: int | None = None, **details: Any) -> None:
    payload: dict[str, Any] = {"event": event}
    if user_id is not None:
        payload["user_id"] = user_id
    payload.update(details)
    logging.getLogger("audit").info("audit_event", extra=payload)

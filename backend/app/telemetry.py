"""Optional OpenTelemetry instrumentation, feature-off by default.

Nothing here may break the stable RAG path: the module is a no-op unless
`MIKURAG_OTEL_ENABLED` is true, degrades to a no-op when the optional `otel`
extra is not installed, and recording helpers swallow exporter errors so a
sick collector can never fail a request or a task. Span attributes and metric
dimensions carry identifiers, versions, counts, durations, and statuses only —
never query, answer, or Document text (see docs/OBSERVABILITY.md).
"""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

try:  # pragma: no cover - the optional `otel` extra decides this branch
    from opentelemetry import trace as _otel_trace

    OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover
    OTEL_AVAILABLE = False

RAG_STAGE_HISTOGRAM = "mikurag.rag.stage.duration"
RAG_TURN_DURATION_HISTOGRAM = "mikurag.rag.turn.duration"
RAG_TURN_COUNTER = "mikurag.rag.turns"
RAG_TOKENS_HISTOGRAM = "mikurag.rag.tokens"
CACHE_OPERATION_COUNTER = "mikurag.cache.operations"
INGESTION_DOCUMENT_COUNTER = "mikurag.ingestion.documents"
INGESTION_DURATION_HISTOGRAM = "mikurag.ingestion.duration"
INGESTION_EMBEDDING_INPUT_COUNTER = "mikurag.ingestion.embedding_inputs"

HEALTH_LIVE_PATH = "/api/v1/health/live"

# Emit both the legacy and stable HTTP semantic conventions so dashboard
# queries can rely on the stable `http.server.request.duration` (seconds)
# metric while existing tooling keeps working.
_DUP_SEMCONV = "http/dup"

_state: dict[str, Any] = {"configured": False, "tracer": None, "instruments": {}}


def is_configured() -> bool:
    return bool(_state["configured"])


@contextmanager
def stage_span(name: str, **attributes: Any) -> Generator[Any, None, None]:
    """Start a child span for a RAG/ingestion stage, or yield None when off."""

    tracer = _state["tracer"]
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        yield span


def attach_request_id(request_id: str) -> None:
    _set_current_span_attributes({"mikurag.request_id": request_id})


def attach_attributes(**attributes: Any) -> None:
    _set_current_span_attributes({k: v for k, v in attributes.items() if v is not None})


def _set_current_span_attributes(attributes: dict[str, Any]) -> None:
    if not _state["configured"]:
        return
    try:
        span = _otel_trace.get_current_span()
        if span is not None and span.is_recording():
            for key, value in attributes.items():
                span.set_attribute(key, value)
    except Exception:  # pragma: no cover - telemetry must never break a request
        logger.debug("Setting span attributes failed", exc_info=True)


def record_stage(stage: str, duration_ms: float) -> None:
    _record(RAG_STAGE_HISTOGRAM, max(0.0, duration_ms) / 1_000, {"stage": stage})


def record_turn(
    outcome: str,
    *,
    total_ms: float | None = None,
    failure_category: str | None = None,
) -> None:
    attributes: dict[str, str] = {"outcome": outcome}
    if failure_category:
        attributes["failure_category"] = failure_category
    _add(RAG_TURN_COUNTER, 1, attributes)
    if total_ms is not None:
        _record(RAG_TURN_DURATION_HISTOGRAM, max(0.0, total_ms) / 1_000, attributes)


def record_token_usage(kind: str, count: int) -> None:
    if count > 0:
        _record(RAG_TOKENS_HISTOGRAM, count, {"kind": kind})


def record_cache_operation(cache: str, result: str) -> None:
    _add(CACHE_OPERATION_COUNTER, 1, {"cache": cache, "result": result})


def record_ingestion_document(
    outcome: str,
    *,
    duration_ms: float,
    embedding_input_count: int,
) -> None:
    _add(INGESTION_DOCUMENT_COUNTER, 1, {"outcome": outcome})
    _record(
        INGESTION_DURATION_HISTOGRAM,
        max(0.0, duration_ms) / 1_000,
        {"outcome": outcome},
    )
    if embedding_input_count > 0:
        _add(INGESTION_EMBEDDING_INPUT_COUNTER, embedding_input_count, {})


def record_turn_measurement(measurement: dict[str, Any], outcome: str) -> None:
    """Mirror a persisted turn measurement into metrics.

    The measurement is already redacted (durations, counts, statuses, model
    versions), so only allowlisted fields are mirrored into metric dimensions.
    """

    latency = measurement.get("latency_ms")
    total_ms: float | None = None
    if isinstance(latency, dict):
        for stage, value in latency.items():
            if stage == "total":
                continue
            if isinstance(value, (int, float)):
                record_stage(stage, float(value))
        total = latency.get("total")
        if isinstance(total, (int, float)):
            total_ms = float(total)
    record_turn(outcome, total_ms=total_ms)
    tokens = measurement.get("tokens")
    if isinstance(tokens, dict):
        for kind, value in tokens.items():
            if isinstance(value, (int, float)):
                record_token_usage(kind, int(value))
    cache = measurement.get("cache")
    if isinstance(cache, dict):
        for cache_name, result in cache.items():
            if isinstance(result, str):
                record_cache_operation(cache_name, result)


def _record(name: str, value: float, attributes: dict[str, str]) -> None:
    instrument = _state["instruments"].get(name)
    if instrument is None:
        return
    try:
        instrument.record(value, attributes)
    except Exception:  # pragma: no cover - telemetry must never break a request
        logger.debug("Recording metric %s failed", name, exc_info=True)


def _add(name: str, value: float, attributes: dict[str, str]) -> None:
    instrument = _state["instruments"].get(name)
    if instrument is None:
        return
    try:
        instrument.add(value, attributes)
    except Exception:  # pragma: no cover - telemetry must never break a request
        logger.debug("Recording metric %s failed", name, exc_info=True)


def _create_instruments(meter: Any) -> None:
    _state["instruments"] = {
        RAG_STAGE_HISTOGRAM: meter.create_histogram(
            RAG_STAGE_HISTOGRAM, unit="s", description="RAG stage latency"
        ),
        RAG_TURN_DURATION_HISTOGRAM: meter.create_histogram(
            RAG_TURN_DURATION_HISTOGRAM, unit="s", description="RAG turn latency"
        ),
        RAG_TURN_COUNTER: meter.create_counter(
            RAG_TURN_COUNTER, description="RAG turn outcomes"
        ),
        RAG_TOKENS_HISTOGRAM: meter.create_histogram(
            RAG_TOKENS_HISTOGRAM, description="Token counts per turn"
        ),
        CACHE_OPERATION_COUNTER: meter.create_counter(
            CACHE_OPERATION_COUNTER, description="Derived-cache read/write results"
        ),
        INGESTION_DOCUMENT_COUNTER: meter.create_counter(
            INGESTION_DOCUMENT_COUNTER, description="Ingestion document outcomes"
        ),
        INGESTION_DURATION_HISTOGRAM: meter.create_histogram(
            INGESTION_DURATION_HISTOGRAM, unit="s", description="Ingestion duration"
        ),
        INGESTION_EMBEDDING_INPUT_COUNTER: meter.create_counter(
            INGESTION_EMBEDDING_INPUT_COUNTER,
            description="Embedding inputs submitted during ingestion",
        ),
    }


def _build_providers(settings: Settings) -> tuple[Any, Any]:
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": _package_version(),
            "deployment.environment.name": settings.environment,
        }
    )
    tracer_provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(settings.otel_trace_sample_ratio)),
    )
    endpoint = settings.otel_exporter_endpoint
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=_signal_endpoint(endpoint, "/v1/traces"),
                timeout=settings.otel_exporter_timeout_seconds,
            )
        )
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=_signal_endpoint(endpoint, "/v1/metrics"),
                    timeout=settings.otel_exporter_timeout_seconds,
                ),
                export_interval_millis=settings.otel_metric_export_interval_ms,
            )
        ],
    )
    return tracer_provider, meter_provider


def _signal_endpoint(base: str, path: str) -> str:
    """Accept either a collector base URL or a full signal endpoint."""

    trimmed = base.rstrip("/")
    return trimmed if trimmed.endswith(path) else f"{trimmed}{path}"


def _instrument_libraries(*, with_fastapi: Any | None) -> None:
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    if with_fastapi is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            with_fastapi, excluded_urls=HEALTH_LIVE_PATH.lstrip("/")
        )
    SQLAlchemyInstrumentor().instrument(engine=_sync_engine())
    RedisInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    CeleryInstrumentor().instrument()


def _sync_engine() -> Any:
    from app.database import engine

    return engine.sync_engine


def setup_telemetry(app: Any | None = None, *, settings: Settings | None = None) -> bool:
    """Install tracing/metrics for the API process. Returns True when active."""

    active_settings = settings or get_settings()
    if not active_settings.otel_enabled or _state["configured"]:
        return is_configured()
    if not OTEL_AVAILABLE:
        logger.warning(
            "MIKURAG_OTEL_ENABLED is true but the OpenTelemetry packages are not "
            "installed; continuing without telemetry (install the 'otel' extra)"
        )
        return False
    try:
        import os

        from opentelemetry import metrics as otel_metrics
        from opentelemetry import trace as otel_trace

        os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", _DUP_SEMCONV)
        tracer_provider, meter_provider = _build_providers(active_settings)
        otel_trace.set_tracer_provider(tracer_provider)
        otel_metrics.set_meter_provider(meter_provider)
        _state["tracer"] = otel_trace.get_tracer("mikurag")
        _create_instruments(otel_metrics.get_meter("mikurag"))
    except Exception:
        logger.exception("OpenTelemetry setup failed; continuing without telemetry")
        return False
    _state["configured"] = True
    try:
        _instrument_libraries(with_fastapi=app)
    except Exception:
        # Providers and application metrics stay active; only the automatic
        # library patches are missing.
        logger.exception("OpenTelemetry library instrumentation failed")
    logger.info(
        "OpenTelemetry enabled (service=%s exporter=%s sample_ratio=%s)",
        active_settings.otel_service_name,
        active_settings.otel_exporter_endpoint,
        active_settings.otel_trace_sample_ratio,
    )
    return True


def instrument_worker(settings: Settings | None = None) -> bool:
    """Install tracing/metrics inside a Celery worker process."""

    return setup_telemetry(None, settings=settings)


def shutdown_telemetry() -> None:
    """Flush and release providers; safe to call when never configured."""

    if not _state["configured"] or not OTEL_AVAILABLE:
        return
    try:
        from opentelemetry import metrics as otel_metrics
        from opentelemetry import trace as otel_trace

        provider = otel_trace.get_tracer_provider()
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            shutdown()
        meter_provider = otel_metrics.get_meter_provider()
        meter_shutdown = getattr(meter_provider, "shutdown", None)
        if callable(meter_shutdown):
            meter_shutdown()
    except Exception:  # pragma: no cover - shutdown must never raise
        logger.debug("OpenTelemetry shutdown failed", exc_info=True)
    finally:
        _state["configured"] = False
        _state["tracer"] = None
        _state["instruments"] = {}


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("mikurag-backend")
    except Exception:  # pragma: no cover - non-installed environments
        return "unknown"

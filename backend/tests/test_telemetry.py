"""OpenTelemetry helpers: flag-gated setup, metrics mirroring, and redaction."""

import logging

import pytest

from app import telemetry
from app.config import Settings

otel_sdk = pytest.importorskip("opentelemetry.sdk")
from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)

SECRET_QUERY = "confidential-project-codename-must-not-leak"


def _secret_free_measurement() -> dict[str, object]:
    return {
        "schema_version": 1,
        "latency_ms": {
            "rewrite": 0.0,
            "query_embedding": 3.0,
            "vector": 5.0,
            "bm25_or_fts": 2.0,
            "fusion": 1.0,
            "rerank": 4.0,
            "expansion": 0.5,
            "generation": 40.0,
            "validation": 1.0,
            "persistence": 2.0,
            "retrieval": 11.0,
            "total": 60.0,
        },
        "tokens": {"prompt": 100, "completion": 20, "query_embedding": 9, "evidence": 500},
        "cache": {"query_embedding": "miss", "retrieval": "disabled"},
        "models": {"embedding": "stub", "generation": "stub"},
    }


@pytest.fixture()
def instrumented_telemetry():
    """Configure telemetry state against in-memory exporters, no globals."""

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])

    telemetry._state["tracer"] = tracer_provider.get_tracer("tests")
    telemetry._create_instruments(meter_provider.get_meter("tests"))
    telemetry._state["configured"] = True
    yield span_exporter, metric_reader
    telemetry._state["configured"] = False
    telemetry._state["tracer"] = None
    telemetry._state["instruments"] = {}


def _counter_total(metric_reader: InMemoryMetricReader, name: str, **attribute_filters) -> int:
    data = metric_reader.get_metrics_data()
    total = 0
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    if all(
                        getattr(point, "attributes", {}).get(key) == value
                        for key, value in attribute_filters.items()
                    ):
                        total += int(point.value) if hasattr(point, "value") else 0
    return total


def _histogram_points(metric_reader: InMemoryMetricReader, name: str) -> list[object]:
    data = metric_reader.get_metrics_data()
    points: list[object] = []
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                if metric.name == name:
                    points.extend(metric.data.data_points)
    return points


def test_disabled_by_default_and_helpers_are_safe_noops() -> None:
    settings = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
    )
    assert settings.otel_enabled is False
    assert telemetry.setup_telemetry(settings=settings) is False
    assert telemetry.is_configured() is False
    with telemetry.stage_span("rag.disabled") as span:
        assert span is None
    telemetry.record_stage("vector", 1.0)
    telemetry.record_turn("grounded_answer", total_ms=5.0)
    telemetry.record_turn_measurement(_secret_free_measurement(), "grounded_answer")
    telemetry.record_cache_operation("query_embedding", "hit")
    telemetry.record_ingestion_document("completed", duration_ms=10.0, embedding_input_count=2)
    telemetry.attach_request_id("ci-noop")


def test_stage_spans_and_metrics_capture_stage_durations(instrumented_telemetry) -> None:
    span_exporter, metric_reader = instrumented_telemetry
    with telemetry.stage_span("rag.retrieve", knowledge_base_id="kb-1") as span:
        assert span is not None
        telemetry.attach_request_id("ci-span-1")
    telemetry.record_stage("vector", 12.0)
    telemetry.record_stage("vector", 8.0)

    finished = span_exporter.get_finished_spans()
    assert [span.name for span in finished] == ["rag.retrieve"]
    assert finished[0].attributes["knowledge_base_id"] == "kb-1"
    assert finished[0].attributes["mikurag.request_id"] == "ci-span-1"

    points = _histogram_points(metric_reader, telemetry.RAG_STAGE_HISTOGRAM)
    vector_points = [
        point for point in points if dict(point.attributes).get("stage") == "vector"
    ]
    assert len(vector_points) == 1
    # Durations are exported in seconds, matching the Prometheus `_seconds`
    # unit suffix applied by the collector's prometheus exporter.
    assert vector_points[0].count == 2
    assert vector_points[0].sum == pytest.approx(0.02)


def test_turn_measurement_is_mirrored_into_metrics(instrumented_telemetry) -> None:
    _, metric_reader = instrumented_telemetry
    telemetry.record_turn_measurement(_secret_free_measurement(), "grounded_answer")

    turns = _counter_total(
        metric_reader,
        telemetry.RAG_TURN_COUNTER,
        outcome="grounded_answer",
    )
    assert turns == 1
    stage_points = _histogram_points(metric_reader, telemetry.RAG_STAGE_HISTOGRAM)
    stages = {dict(point.attributes)["stage"] for point in stage_points}
    assert {"rewrite", "query_embedding", "vector", "generation", "persistence"} <= stages
    assert "total" not in stages
    token_points = _histogram_points(metric_reader, telemetry.RAG_TOKENS_HISTOGRAM)
    kinds = {dict(point.attributes)["kind"] for point in token_points}
    assert {"prompt", "completion", "query_embedding", "evidence"} <= kinds
    assert _counter_total(
        metric_reader,
        telemetry.CACHE_OPERATION_COUNTER,
        cache="query_embedding",
        result="miss",
    ) == 1


def test_turn_measurement_metrics_never_expose_private_text(instrumented_telemetry) -> None:
    _, metric_reader = instrumented_telemetry
    measurement = {
        **_secret_free_measurement(),
        # An unexpected private field must never become a metric dimension.
        "query": SECRET_QUERY,
    }
    telemetry.record_turn_measurement(measurement, "grounded_answer")

    data = metric_reader.get_metrics_data()
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                for point in metric.data.data_points:
                    for key, value in dict(point.attributes).items():
                        assert key in {
                            "stage",
                            "outcome",
                            "failure_category",
                            "kind",
                            "cache",
                            "result",
                        }
                        assert not (
                            isinstance(value, str) and len(value) > 64
                        ), f"{metric.name}/{key}"
    assert SECRET_QUERY not in repr(data)


def test_failed_turns_record_the_failure_category(instrumented_telemetry) -> None:
    _, metric_reader = instrumented_telemetry
    telemetry.record_turn("failed", failure_category="provider_or_grounding_error")
    assert _counter_total(
        metric_reader,
        telemetry.RAG_TURN_COUNTER,
        outcome="failed",
        failure_category="provider_or_grounding_error",
    ) == 1


def test_recording_survives_an_unreachable_exporter(caplog) -> None:
    """Failure drill: a dead collector must not break requests or tasks."""

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    closed_port = "http://127.0.0.1:1"
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{closed_port}/v1/traces", timeout=1),
        )
    )
    telemetry._state["tracer"] = tracer_provider.get_tracer("tests-drill")
    telemetry._state["instruments"] = {}
    telemetry._state["configured"] = True
    try:
        with telemetry.stage_span("rag.retrieve") as span:
            assert span is not None
        logging.getLogger("drill").info("request continues without telemetry")
    finally:
        telemetry._state["configured"] = False
        telemetry._state["tracer"] = None


def test_signal_endpoint_helper_accepts_base_and_full_urls() -> None:
    assert telemetry._signal_endpoint("http://collector:4318", "/v1/traces") == (
        "http://collector:4318/v1/traces"
    )
    assert telemetry._signal_endpoint("http://collector:4318/", "/v1/traces") == (
        "http://collector:4318/v1/traces"
    )
    assert telemetry._signal_endpoint("http://collector:4318/v1/traces", "/v1/traces") == (
        "http://collector:4318/v1/traces"
    )


def test_shutdown_resets_state_and_is_safe_when_never_configured() -> None:
    telemetry.shutdown_telemetry()
    assert telemetry.is_configured() is False


def test_measurement_stage_names_cover_every_reported_stage() -> None:
    """The mirrored metric dimensions stay aligned with the measurement schema."""

    measurement = _secret_free_measurement()
    latency = measurement["latency_ms"]
    assert isinstance(latency, dict)
    assert set(latency) == {
        "rewrite",
        "query_embedding",
        "vector",
        "bm25_or_fts",
        "fusion",
        "rerank",
        "expansion",
        "generation",
        "validation",
        "persistence",
        "retrieval",
        "total",
    }

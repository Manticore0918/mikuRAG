"""Failure drills: provider outages stay safe and never take the app down.

The plan's failure-drill matrix maps to existing suites (Redis fail-open in
test_rag_cache, readiness errors in test_health, reranker and FTS fallbacks in
test_retrieval_modes, malformed-parser recovery in the smoke scripts). This
module covers the remaining drills: the embedding and generation providers
failing must raise typed errors with safe messages (no endpoints, no secrets),
and the turn-failure path must still emit observations and telemetry metrics.
"""

import json
import logging

import httpx
import pytest

from app import telemetry
from app.config import Settings
from app.ingestion.embeddings import embed_texts
from app.ingestion.errors import EmbeddingProviderError
from app.observability import OBSERVATION_PREFIX, emit_observation
from app.rag.generation import GenerationProviderError, complete_json, stream_json


def _drill_settings(**overrides: object) -> Settings:
    return Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        embedding_api_key="drill-key",
        **overrides,
    )


@pytest.fixture()
def instrumented_metrics():
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    telemetry._state["tracer"] = None
    telemetry._create_instruments(MeterProvider(metric_readers=[reader]).get_meter("drill"))
    telemetry._state["configured"] = True
    yield reader
    telemetry._state["configured"] = False
    telemetry._state["instruments"] = {}


async def test_embedding_outage_raises_safe_typed_error() -> None:
    drill_settings = _drill_settings(embedding_endpoint="https://127.0.0.1:1/embed")
    async with httpx.AsyncClient(timeout=1) as client:
        with pytest.raises(EmbeddingProviderError) as error:
            await embed_texts(["question"], drill_settings, client)
    message = str(error.value)
    assert "embedding provider" in message.lower()
    assert "127.0.0.1" not in message
    assert "drill-key" not in message


async def test_generation_outage_raises_safe_typed_error() -> None:
    drill_settings = _drill_settings(generation_base_url="https://127.0.0.1:1/v1")
    with pytest.raises(GenerationProviderError) as complete_error:
        await complete_json([{"role": "user", "content": "q"}], drill_settings)
    assert "generation provider" in str(complete_error.value).lower()
    with pytest.raises(GenerationProviderError):
        await stream_json([{"role": "user", "content": "q"}], drill_settings)


def test_turn_failure_path_still_records_observations_and_metrics(
    caplog, instrumented_metrics
) -> None:
    """The instrumentation on the failure path must never be the next failure."""

    logger = logging.getLogger("tests.failure_drills")
    reader = instrumented_metrics
    with caplog.at_level(logging.INFO, logger=logger.name):
        emit_observation(
            logger,
            "rag_turn_failure",
            conversation_id="00000000-0000-0000-0000-000000000000",
            terminal_stage="generating",
            failure_category="provider_or_grounding_error",
        )
        telemetry.record_turn("failed", failure_category="provider_or_grounding_error")

    message = caplog.records[-1].getMessage()
    payload = json.loads(message.removeprefix(OBSERVATION_PREFIX))
    assert payload["failure_category"] == "provider_or_grounding_error"

    data = reader.get_metrics_data()
    recorded = [
        metric.name
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    ]
    assert telemetry.RAG_TURN_COUNTER in recorded


def test_unexpected_attribute_failure_on_the_metrics_path_is_swallowed(
    instrumented_metrics,
) -> None:
    """A broken attribute value must not propagate into the request path."""

    class BrokenHistogram:
        def record(self, value: float, attributes: dict) -> None:
            raise RuntimeError("boom")

    telemetry._state["instruments"][telemetry.RAG_STAGE_HISTOGRAM] = BrokenHistogram()
    telemetry.record_stage("vector", 1.0)

"""Correlation identifiers: HTTP middleware, log records, and Celery hops."""

import json
import logging
from unittest.mock import MagicMock

from celery.signals import before_task_publish, task_postrun, task_prerun
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import correlation
from app.correlation import (
    CELERY_CORRELATION_HEADER,
    CORRELATION_HEADER,
    CorrelationIdMiddleware,
    connect_celery_signals,
    current_correlation_id,
    install_record_factory,
    normalize_correlation_id,
)
from app.observability import OBSERVATION_PREFIX, emit_observation


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"request_id": current_correlation_id()}

    return app


def test_normalize_accepts_safe_and_rejects_unsafe_values() -> None:
    assert normalize_correlation_id("01H8ZK3P6Q-client.example/1") is not None
    assert normalize_correlation_id("a" * 128) is not None
    assert normalize_correlation_id("") is None
    assert normalize_correlation_id("not safe id") is None
    assert normalize_correlation_id("x" * 129) is None
    assert normalize_correlation_id("<script>") is None
    assert normalize_correlation_id(42) is None


async def test_inbound_request_id_is_adopted_and_echoed() -> None:
    app = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/ping", headers={CORRELATION_HEADER: "ci-test-123"})
    assert response.status_code == 200
    assert response.headers[CORRELATION_HEADER] == "ci-test-123"
    assert response.json()["request_id"] == "ci-test-123"


async def test_missing_request_id_is_generated() -> None:
    app = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/ping")
        second = await client.get("/ping")
    assert first.json()["request_id"] != second.json()["request_id"]
    assert first.headers[CORRELATION_HEADER] == first.json()["request_id"]


async def test_unsafe_request_id_is_replaced() -> None:
    app = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/ping", headers={CORRELATION_HEADER: "garbage id with spaces"}
        )
    generated = response.headers[CORRELATION_HEADER]
    assert generated != "garbage id with spaces"
    assert normalize_correlation_id(generated) == generated


def test_install_record_factory_is_idempotent() -> None:
    install_record_factory()
    first = logging.getLogRecordFactory()
    install_record_factory()
    assert logging.getLogRecordFactory() is first


def test_log_records_carry_the_active_correlation_id(caplog) -> None:
    install_record_factory()
    logger = logging.getLogger("tests.correlation")
    with caplog.at_level(logging.INFO, logger=logger.name):
        token = correlation.set_correlation_id("ci-log-1")
        try:
            logger.info("hello")
        finally:
            correlation.reset_correlation_id(token)
        logger.info("outside")
    assert caplog.records[0].correlation_id == "ci-log-1"
    assert caplog.records[1].correlation_id is None


def test_observation_includes_correlation_id_only_when_set(caplog) -> None:
    logger = logging.getLogger("tests.correlation.observation")
    with caplog.at_level(logging.INFO, logger=logger.name):
        emit_observation(logger, "background_event", count=1)
        token = correlation.set_correlation_id("ci-obs-1")
        try:
            emit_observation(logger, "request_event", count=2)
        finally:
            correlation.reset_correlation_id(token)

    background = json.loads(
        caplog.records[0].getMessage().removeprefix(OBSERVATION_PREFIX)
    )
    request_scoped = json.loads(
        caplog.records[1].getMessage().removeprefix(OBSERVATION_PREFIX)
    )
    assert "correlation_id" not in background
    assert request_scoped["correlation_id"] == "ci-obs-1"
    assert request_scoped["count"] == 2


def test_celery_publish_stamps_and_worker_adopts_the_correlation_id() -> None:
    connect_celery_signals()
    headers: dict = {}
    token = correlation.set_correlation_id("ci-celery-1")
    try:
        before_task_publish.send(sender="mikurag.documents.ingest", headers=headers)
    finally:
        correlation.reset_correlation_id(token)
    assert headers[CELERY_CORRELATION_HEADER] == "ci-celery-1"

    task = MagicMock()
    task.request.headers = dict(headers)
    task_prerun.send(sender=task, task=task)
    assert current_correlation_id() == "ci-celery-1"
    task_postrun.send(sender=task, task=task)
    assert current_correlation_id() == ""


def test_worker_generates_a_correlation_id_when_the_header_is_missing() -> None:
    connect_celery_signals()
    task = MagicMock()
    task.request.headers = {}
    task_prerun.send(sender=task, task=task)
    adopted = current_correlation_id()
    assert normalize_correlation_id(adopted) == adopted
    task_postrun.send(sender=task, task=task)
    assert current_correlation_id() == ""

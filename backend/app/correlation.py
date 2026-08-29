"""Request and job correlation across API and Celery boundaries.

Every HTTP request adopts a validated `X-Request-ID` header or generates one,
stores it in a ContextVar, and echoes it on the response. The identifier is
injected into every log record, propagated to Celery tasks through message
headers, and attached to telemetry spans so one request can be followed across
API, worker, database, cache, and model calls. Inbound values are validated and
unusable values are replaced, so the identifier can never smuggle private or
oversized content into logs.
"""

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from typing import Any

CORRELATION_HEADER = "X-Request-ID"
CORRELATION_ATTRIBUTE = "request_id"
CELERY_CORRELATION_HEADER = "mikurag-correlation-id"

MAX_CORRELATION_LENGTH = 128
_SAFE_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")

_correlation_id: ContextVar[str] = ContextVar("mikurag_correlation_id", default="")


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def normalize_correlation_id(value: object) -> str | None:
    """Return a safe correlation identifier, or None when unusable."""

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if _SAFE_CORRELATION_ID.fullmatch(candidate):
        return candidate
    return None


def current_correlation_id() -> str:
    return _correlation_id.get()


def set_correlation_id(value: str) -> Token[str]:
    return _correlation_id.set(value)


def reset_correlation_id(token: Token[str]) -> None:
    _correlation_id.reset(token)


_previous_record_factory: Callable[..., logging.LogRecord] | None = None


def _correlation_record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = (
        _previous_record_factory(*args, **kwargs)
        if _previous_record_factory is not None
        else logging.LogRecord(*args, **kwargs)
    )
    record.correlation_id = current_correlation_id() or None
    return record


def install_record_factory() -> None:
    """Attach the correlation identifier to every created log record.

    A record factory is used instead of logger/handler filters so the
    attribute is present regardless of how uvicorn, Celery, or the
    application configure logging. Installing twice is a no-op.
    """

    global _previous_record_factory
    if _previous_record_factory is not None:
        return
    _previous_record_factory = logging.getLogRecordFactory()
    logging.setLogRecordFactory(_correlation_record_factory)


class CorrelationIdMiddleware:
    """Pure ASGI middleware that assigns and echoes the correlation ID."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        header_value = _header_value(scope.get("headers") or [])
        correlation_id = normalize_correlation_id(header_value) or new_correlation_id()
        token = set_correlation_id(correlation_id)

        async def send_with_correlation(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((CORRELATION_HEADER.lower().encode(), correlation_id.encode()))
            await send(message)

        try:
            # The OpenTelemetry HTTP server span (when enabled) is the current
            # span inside this middleware, so the identifier joins both worlds.
            from app.telemetry import attach_request_id  # noqa: PLC0415

            attach_request_id(correlation_id)
            await self.app(scope, receive, send_with_correlation)
        finally:
            reset_correlation_id(token)


def _header_value(raw_headers: list[tuple[bytes, bytes]]) -> str | None:
    matches = [
        value.decode("latin-1")
        for name, value in raw_headers
        if name.lower() == CORRELATION_HEADER.lower().encode()
    ]
    return matches[-1] if matches else None


_celery_signals_connected = False


def connect_celery_signals() -> None:
    """Propagate the correlation ID into and out of Celery task execution.

    Publishers stamp the current identifier (or a fresh one) into the task
    message headers; workers adopt it in `task_prerun` so task logs,
    observations, and spans carry the initiating request's identifier.
    """

    global _celery_signals_connected
    if _celery_signals_connected:
        return
    from celery.signals import (  # noqa: PLC0415
        before_task_publish,
        task_postrun,
        task_prerun,
    )

    # Handlers are nested, so they must be strongly referenced: Celery stores
    # receivers as weak references and would drop them as soon as this
    # function returns.
    @before_task_publish.connect(weak=False)
    def _stamp_correlation_id(
        sender: str | None = None,
        headers: dict | None = None,
        **_: Any,
    ) -> None:
        if headers is None:
            return
        correlation_id = current_correlation_id() or new_correlation_id()
        headers[CELERY_CORRELATION_HEADER] = correlation_id

    _prerun_tokens: dict[int, Token[str]] = {}

    @task_prerun.connect(weak=False)
    def _adopt_correlation_id(task: Any = None, **_: Any) -> None:
        if task is None:
            return
        task_headers = getattr(task.request, "headers", None) or {}
        correlation_id = normalize_correlation_id(
            task_headers.get(CELERY_CORRELATION_HEADER)
        ) or new_correlation_id()
        _prerun_tokens[id(task)] = set_correlation_id(correlation_id)

    @task_postrun.connect(weak=False)
    def _release_correlation_id(task: Any = None, **_: Any) -> None:
        if task is None:
            return
        token = _prerun_tokens.pop(id(task), None)
        if token is not None:
            reset_correlation_id(token)

    _celery_signals_connected = True

"""Typed query planning: promote the follow-up rewrite into a `QueryPlan`.

The rewrite step is bounded by a wall-clock timeout and always falls back to the
original question, so a slow or invalid rewrite can never block retrieval. The
plan carries the rewritten query, any inferred metadata filters, preserved
identifiers, and a rewrite status; retrieval consumes the plan's effective
query and inferred filters.
"""

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from app.config import Settings
from app.rag.generation import GenerationProviderError
from app.rag.grounding import (
    GroundingValidationError,
    HistoryMessage,
    QueryRewrite,
    rewrite_messages,
)
from app.rag.retrieval_types import (
    QueryPlan,
    RetrievalFilters,
    RetrievalMetrics,
    RewriteStatus,
)

logger = logging.getLogger(__name__)

FOLLOW_UP_PREFIX = re.compile(
    r"^\s*(?:and\b|also\b|what about\b|how about\b|what else\b)", re.IGNORECASE
)
FOLLOW_UP_REFERENCE = re.compile(
    r"\b(?:it|its|this|that|these|those|they|them|same|former|latter)\b",
    re.IGNORECASE,
)
CONCRETE_IDENTIFIER = re.compile(
    r"\b(?=[A-Za-z0-9._:/-]*[A-Za-z])(?=[A-Za-z0-9._:/-]*\d)"
    r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,}\b"
)


def should_rewrite(question: str, history: list[HistoryMessage]) -> bool:
    """A short referential follow-up is the only rewrite-eligible case.

    Standalone questions and long prompts keep the original query unchanged,
    which preserves today's behavior for the common case.
    """
    is_short_follow_up = len(question.split()) <= 20 and (
        FOLLOW_UP_PREFIX.search(question) is not None
        or FOLLOW_UP_REFERENCE.search(question) is not None
    )
    return bool(history) and is_short_follow_up


def _parse_uuids(values: list[str]) -> tuple[uuid.UUID, ...]:
    parsed: list[uuid.UUID] = []
    for value in values:
        try:
            parsed.append(uuid.UUID(str(value)))
        except (ValueError, TypeError):
            logger.warning("Ignoring non-UUID document identifier in rewrite: %r", value)
    return tuple(parsed)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Ignoring invalid ingested date in rewrite: %r", value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def parse_query_rewrite(
    payload: object,
    *,
    original_query: str,
    required_identifiers: tuple[str, ...] = (),
) -> QueryPlan:
    """Validate a rewrite payload into a `QueryPlan`, dropping unparseable details.

    Raises `GroundingValidationError` when the model returns something that is
    not a valid query at all; the caller falls back to the original question.
    """
    try:
        rewrite = QueryRewrite.model_validate(payload)
    except ValidationError as error:
        raise GroundingValidationError("The follow-up rewrite was invalid") from error

    rewritten_query = rewrite.query.strip()
    if not rewritten_query:
        raise GroundingValidationError("The follow-up rewrite was empty")
    preserved = tuple(
        dict.fromkeys(
            [
                *(item.strip() for item in rewrite.preserved_identifiers if item.strip()),
                *required_identifiers,
                *CONCRETE_IDENTIFIER.findall(original_query),
            ]
        )
    )
    missing = [item for item in preserved if item not in rewritten_query]
    if missing:
        raise GroundingValidationError(
            "The follow-up rewrite dropped preserved identifiers: "
            + ", ".join(missing)
        )

    inferred = rewrite.inferred_filters
    ingested_after = _parse_datetime(inferred.ingested_after) if inferred else None
    ingested_before = _parse_datetime(inferred.ingested_before) if inferred else None
    if (
        ingested_after is not None
        and ingested_before is not None
        and ingested_after > ingested_before
    ):
        raise GroundingValidationError("The inferred ingestion date range was invalid")
    filters = (
        RetrievalFilters(
            document_ids=_parse_uuids(inferred.document_ids),
            tags=tuple(inferred.tags),
            source_kinds=tuple(inferred.source_kinds),
            languages=tuple(inferred.languages),
            ingested_after=ingested_after,
            ingested_before=ingested_before,
        )
        if inferred is not None
        else RetrievalFilters()
    )
    return QueryPlan(
        original_query=original_query,
        rewritten_query=rewritten_query,
        inferred_filters=filters,
        preserved_identifiers=preserved,
        status=RewriteStatus.REWRITTEN,
    )


async def build_query_plan(
    question: str,
    history: list[HistoryMessage],
    settings: Settings,
    *,
    complete: Callable[[list[dict[str, str]]], Awaitable[Any]] | None = None,
    metrics: RetrievalMetrics | None = None,
) -> tuple[QueryPlan, dict[str, int]]:
    """Build the typed plan, bounding the rewrite by a wall-clock timeout.

    When the question is not rewrite-eligible the plan is returned unchanged
    without calling the provider. On any provider, validation, or timeout
    failure the plan keeps the original query with `REWRITE_FAILED`, and usage
    is reported as empty so telemetry never surfaces a partial rewrite.
    """
    if not should_rewrite(question, history):
        if metrics is not None:
            metrics.rewrite_status = RewriteStatus.UNCHANGED.value
        return QueryPlan(original_query=question), {}

    if complete is None:
        raise TypeError("complete is required when a rewrite is eligible")

    started = perf_counter()
    usage: dict[str, int] = {}
    plan: QueryPlan
    try:
        rewrite = await asyncio.wait_for(
            complete(rewrite_messages(question, history)),
            timeout=settings.query_rewrite_timeout_seconds,
        )
        required_identifiers = tuple(
            dict.fromkeys(
                identifier
                for content in [question, *(item.content for item in history)]
                for identifier in CONCRETE_IDENTIFIER.findall(content)
            )
        )
        plan = parse_query_rewrite(
            rewrite.payload,
            original_query=question,
            required_identifiers=required_identifiers,
        )
        usage = dict(rewrite.usage)
    except (GenerationProviderError, GroundingValidationError, TimeoutError) as error:
        logger.warning("Using the original question after query rewrite failed: %s", error)
        plan = QueryPlan(original_query=question, status=RewriteStatus.REWRITE_FAILED)
    if metrics is not None:
        metrics.rewrite_latency_ms = (perf_counter() - started) * 1_000
        metrics.rewrite_status = plan.status.value
    return plan, usage

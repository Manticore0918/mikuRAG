"""Tests for typed query planning and the rewrite fallback.

The rewrite provider is injected through the `complete` callable so provider
failures, invalid JSON, and timeouts are exercised without a model. The core
contract under test: the plan always carries the original question, a slow or
invalid rewrite never blocks retrieval, and inferred filters are parsed
defensively (bad UUIDs and dates are dropped, not fatal).
"""

import asyncio
import uuid

import pytest

from app.config import Settings
from app.rag import query_plan as query_plan_module
from app.rag.generation import GenerationProviderError, GenerationResult
from app.rag.grounding import GroundingValidationError, HistoryMessage
from app.rag.query_plan import build_query_plan, parse_query_rewrite, should_rewrite
from app.rag.retrieval_types import RetrievalMetrics, RewriteStatus


def settings(**overrides) -> Settings:
    return Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        **overrides,
    )


def history() -> list[HistoryMessage]:
    return [HistoryMessage(role="user", content="How many remote days are allowed?")]


def result(payload: object, usage: dict[str, int] | None = None) -> GenerationResult:
    return GenerationResult(payload=payload, usage=usage or {})


def test_should_rewrite_only_for_short_referential_follow_ups() -> None:
    standalone = "What is the Tokyo hotel limit?"
    follow_up = "Does that apply during the second week?"

    assert not should_rewrite(standalone, history())
    assert should_rewrite(follow_up, history())
    assert not should_rewrite(follow_up, [])


def test_parse_query_rewrite_builds_a_typed_plan() -> None:
    document_id = uuid.uuid4()
    plan = parse_query_rewrite(
        {
            "query": "Remote Work Policy HR leave policy",
            "inferred_filters": {
                "tags": ["hr"],
                "source_kinds": ["pdf"],
                "languages": ["en"],
                "document_ids": [str(document_id)],
                "ingested_after": "2026-03-01T00:00:00+00:00",
                "ingested_before": "2026-03-31T00:00:00+00:00",
            },
            "preserved_identifiers": ["Remote Work Policy"],
        },
        original_query="that policy",
    )

    assert plan.original_query == "that policy"
    assert plan.rewritten_query == "Remote Work Policy HR leave policy"
    assert plan.status == RewriteStatus.REWRITTEN
    assert plan.inferred_filters.tags == ("hr",)
    assert plan.inferred_filters.source_kinds == ("pdf",)
    assert plan.inferred_filters.languages == ("en",)
    assert plan.inferred_filters.document_ids == (document_id,)
    assert plan.inferred_filters.ingested_after is not None
    assert plan.inferred_filters.ingested_before is not None
    assert plan.preserved_identifiers == ("Remote Work Policy",)


def test_parse_query_rewrite_drops_invalid_uuids_and_dates() -> None:
    plan = parse_query_rewrite(
        {
            "query": "leave",
            "inferred_filters": {
                "document_ids": ["not-a-uuid"],
                "ingested_after": "not-a-date",
            },
        },
        original_query="original",
    )

    assert plan.inferred_filters.document_ids == ()
    assert plan.inferred_filters.ingested_after is None
    assert plan.status == RewriteStatus.REWRITTEN


def test_parse_query_rewrite_without_filters_stays_empty() -> None:
    plan = parse_query_rewrite({"query": "standalone access policy"}, original_query="x")

    assert plan.rewritten_query == "standalone access policy"
    assert plan.inferred_filters.is_empty()
    assert plan.preserved_identifiers == ()


def test_parse_query_rewrite_rejects_a_non_query_payload() -> None:
    with pytest.raises(GroundingValidationError):
        parse_query_rewrite({"answer": "not a query"}, original_query="x")


def test_parse_query_rewrite_rejects_dropped_model_identifiers() -> None:
    with pytest.raises(GroundingValidationError, match="Remote Work Policy"):
        parse_query_rewrite(
            {
                "query": "HR leave policy",
                "preserved_identifiers": ["Remote Work Policy"],
            },
            original_query="that policy",
        )


def test_parse_query_rewrite_preserves_concrete_identifiers_from_question() -> None:
    plan = parse_query_rewrite(
        {"query": "Explain ERR-417 recovery", "preserved_identifiers": []},
        original_query="What about ERR-417?",
    )

    assert plan.preserved_identifiers == ("ERR-417",)


def test_parse_query_rewrite_rejects_dropped_concrete_question_identifier() -> None:
    with pytest.raises(GroundingValidationError, match="ERR-417"):
        parse_query_rewrite(
            {"query": "Explain recovery", "preserved_identifiers": []},
            original_query="What about ERR-417?",
        )


def test_parse_query_rewrite_rejects_an_inverted_inferred_date_range() -> None:
    with pytest.raises(GroundingValidationError, match="date range"):
        parse_query_rewrite(
            {
                "query": "current policy",
                "inferred_filters": {
                    "ingested_after": "2026-08-03T00:00:00Z",
                    "ingested_before": "2026-08-01T00:00:00Z",
                },
            },
            original_query="that policy",
        )


@pytest.mark.asyncio
async def test_build_plan_skips_rewrite_when_not_eligible() -> None:
    async def unexpected_rewrite(_: object) -> GenerationResult:
        raise AssertionError("non-eligible questions must not call the provider")

    plan, usage = await build_query_plan(
        "What is the Tokyo hotel limit?",
        history(),
        settings(),
        complete=unexpected_rewrite,
    )

    assert plan.status == RewriteStatus.UNCHANGED
    assert plan.effective_query == "What is the Tokyo hotel limit?"
    assert usage == {}


@pytest.mark.asyncio
async def test_build_plan_uses_rewrite_and_preserves_usage() -> None:
    async def rewrite(_: object) -> GenerationResult:
        return result({"query": "HR leave policy"}, usage={"total_tokens": 12})

    plan, usage = await build_query_plan(
        "that policy",
        history(),
        settings(),
        complete=rewrite,
    )

    assert plan.status == RewriteStatus.REWRITTEN
    assert plan.rewritten_query == "HR leave policy"
    assert plan.effective_query == "HR leave policy"
    assert usage == {"total_tokens": 12}


@pytest.mark.asyncio
async def test_build_plan_requires_identifiers_from_history_in_the_rewrite() -> None:
    async def rewrite(_: object) -> GenerationResult:
        return result({"query": "Explain the recovery procedure"})

    plan, usage = await build_query_plan(
        "What about it?",
        [HistoryMessage(role="user", content="Explain incident ERR-417")],
        settings(),
        complete=rewrite,
    )

    assert plan.status == RewriteStatus.REWRITE_FAILED
    assert plan.effective_query == "What about it?"
    assert usage == {}


@pytest.mark.asyncio
async def test_build_plan_falls_back_on_invalid_rewrite() -> None:
    async def invalid(_: object) -> GenerationResult:
        return result({"answer": "not a query"}, usage={"total_tokens": 5})

    plan, usage = await build_query_plan(
        "that policy",
        history(),
        settings(),
        complete=invalid,
    )

    assert plan.status == RewriteStatus.REWRITE_FAILED
    assert plan.effective_query == "that policy"
    assert usage == {}


@pytest.mark.asyncio
async def test_build_plan_falls_back_on_provider_error() -> None:
    async def failing(_: object) -> GenerationResult:
        raise GenerationProviderError("model returned invalid JSON")

    plan, usage = await build_query_plan(
        "that policy",
        history(),
        settings(),
        complete=failing,
    )

    assert plan.status == RewriteStatus.REWRITE_FAILED
    assert plan.effective_query == "that policy"


@pytest.mark.asyncio
async def test_build_plan_falls_back_on_timeout(monkeypatch) -> None:
    async def slow(_: object) -> GenerationResult:
        await asyncio.sleep(0.5)
        return result({"query": "rewritten"}, usage={})

    async def always_timeout(awaitable, **kwargs):
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise TimeoutError()

    monkeypatch.setattr(query_plan_module.asyncio, "wait_for", always_timeout)

    plan, usage = await build_query_plan(
        "that policy",
        history(),
        settings(),
        complete=slow,
    )

    assert plan.status == RewriteStatus.REWRITE_FAILED
    assert plan.effective_query == "that policy"
    assert usage == {}


@pytest.mark.asyncio
async def test_build_plan_records_status_and_latency_metrics() -> None:
    async def rewrite(_: object) -> GenerationResult:
        return result({"query": "HR leave policy"})

    metrics = RetrievalMetrics()
    await build_query_plan(
        "that policy",
        history(),
        settings(),
        complete=rewrite,
        metrics=metrics,
    )

    assert metrics.rewrite_status == RewriteStatus.REWRITTEN.value
    assert metrics.rewrite_latency_ms > 0


@pytest.mark.asyncio
async def test_build_plan_requires_a_provider_only_when_eligible() -> None:
    with pytest.raises(TypeError):
        await build_query_plan(
            "that policy",
            history(),
            settings(),
        )

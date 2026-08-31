"""Tests for the local cross-encoder reranker provider.

The sentence-transformers model is never loaded in tests: an `encoder_factory`
injects a fake encoder so batch handling, ordering, timeout, concurrency, and
failure fallback can be exercised without the optional `rerank` extra.
"""

import asyncio
import time
import uuid

import pytest

from app.config import Settings
from app.models import Chunk, ChunkLevel, Document, DocumentStatus
from app.rag.reranking import (
    CrossEncoderReranker,
    RerankerProviderError,
    build_default_reranker,
    build_reranker,
)
from app.rag.retrieval_types import Candidate, RetrievalMetrics, RetrievalMode


def candidate(text: str) -> Candidate:
    return Candidate(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_name="policy.md",
        locator={"page": 1},
        text=text,
        semantic_similarity=0.5,
        lexical_score=0.1,
        fused_score=0.2,
    )


class FakeEncoder:
    """A stand-in for sentence_transformers.CrossEncoder.predict."""

    def __init__(
        self,
        scores: list[float],
        *,
        sleep_seconds: float = 0.0,
        fail: bool = False,
    ) -> None:
        self.scores = scores
        self.sleep_seconds = sleep_seconds
        self.fail = fail
        self.predict_calls: list[tuple] = []
        self.active = 0
        self.max_active = 0

    def predict(self, pairs, *, batch_size: int, show_progress_bar: bool):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.predict_calls.append((pairs, batch_size, show_progress_bar))
            if self.sleep_seconds:
                time.sleep(self.sleep_seconds)
            if self.fail:
                raise RuntimeError("encoder predict failed")
            return self.scores
        finally:
            self.active -= 1


def make_reranker(
    encoder: FakeEncoder,
    *,
    batch_size: int = 8,
    timeout_seconds: float = 15.0,
    max_concurrency: int = 1,
) -> CrossEncoderReranker:
    return CrossEncoderReranker(
        model_name="test-model",
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
        max_concurrency=max_concurrency,
        encoder_factory=lambda: encoder,
    )


@pytest.mark.asyncio
async def test_rerank_uses_batch_predict_and_sorts_by_score() -> None:
    first = candidate("unrelated")
    second = candidate("closest match")
    third = candidate("middle")
    encoder = FakeEncoder([0.1, 0.9, 0.5])
    reranker = make_reranker(encoder, batch_size=8)

    reranked = await reranker.rerank("query", [first, second, third])

    assert encoder.predict_calls[0][0] == [
        ("query", "unrelated"),
        ("query", "closest match"),
        ("query", "middle"),
    ]
    assert encoder.predict_calls[0][1] == 8
    assert encoder.predict_calls[0][2] is False
    assert [item.chunk_id for item in reranked] == [
        second.chunk_id,
        third.chunk_id,
        first.chunk_id,
    ]
    assert reranked[0].rerank_score == 0.9


@pytest.mark.asyncio
async def test_empty_candidates_skip_inference() -> None:
    encoder = FakeEncoder([])
    reranker = make_reranker(encoder)

    result = await reranker.rerank("query", [])

    assert result == []
    assert encoder.predict_calls == []


@pytest.mark.asyncio
async def test_warmup_loads_model_and_runs_inference_before_ranking() -> None:
    encoder = FakeEncoder([0.25])
    reranker = make_reranker(encoder)

    await reranker.warmup()
    await reranker.rerank("query", [candidate("passage")])

    assert encoder.predict_calls[0][0] == [
        ("reranker warmup query", "reranker warmup passage")
    ]
    assert encoder.predict_calls[1][0] == [("query", "passage")]


@pytest.mark.asyncio
async def test_timeout_raises_provider_error_for_fallback() -> None:
    encoder = FakeEncoder([0.9], sleep_seconds=0.3)
    reranker = make_reranker(encoder, timeout_seconds=0.05)

    with pytest.raises(RerankerProviderError):
        await reranker.rerank("query", [candidate("slow")])


@pytest.mark.asyncio
async def test_predict_failure_raises_provider_error_for_fallback() -> None:
    encoder = FakeEncoder([], fail=True)
    reranker = make_reranker(encoder)

    with pytest.raises(RerankerProviderError):
        await reranker.rerank("query", [candidate("fails")])


@pytest.mark.asyncio
async def test_missing_model_raises_provider_error_without_importing_st() -> None:
    reranker = CrossEncoderReranker(encoder_factory=lambda: None)

    with pytest.raises(RerankerProviderError):
        await reranker.rerank("query", [candidate("no model")])


@pytest.mark.asyncio
async def test_concurrency_semaphore_limits_simultaneous_inference() -> None:
    encoder = FakeEncoder([0.9], sleep_seconds=0.03)
    reranker = make_reranker(encoder, max_concurrency=1)

    await asyncio.gather(*[reranker.rerank("query", [candidate("one")]) for _ in range(4)])

    assert encoder.max_active == 1


@pytest.mark.asyncio
async def test_default_reranker_is_deterministic() -> None:
    settings = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
    )
    assert build_default_reranker(settings).provider_name == "deterministic"


@pytest.mark.asyncio
async def test_build_selects_cross_encoder_when_configured() -> None:
    settings = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        reranker_provider="cross_encoder",
    )
    reranker = build_default_reranker(settings)
    assert reranker.provider_name == "cross_encoder"
    assert reranker.model_name == "cross-encoder/ms-marco-MiniLM-L-6-v2"


def test_build_reranker_uses_provider_argument_over_settings() -> None:
    default_settings = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
    )
    cross_settings = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        reranker_provider="cross_encoder",
    )

    assert build_reranker("cross_encoder", default_settings).provider_name == "cross_encoder"
    assert build_reranker("deterministic", cross_settings).provider_name == "deterministic"


def test_build_reranker_none_delegates_to_configured_provider() -> None:
    default_settings = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
    )
    cross_settings = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        reranker_provider="cross_encoder",
    )

    assert build_reranker(None, default_settings).provider_name == "deterministic"
    assert build_reranker(None, cross_settings).provider_name == "cross_encoder"
    assert build_default_reranker(cross_settings).provider_name == "cross_encoder"


def test_default_reranker_is_reused_across_turns() -> None:
    configured = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        reranker_provider="cross_encoder",
        reranker_model="cache-test-model",
    )

    assert build_default_reranker(configured) is build_default_reranker(configured)


@pytest.mark.asyncio
async def test_timeout_keeps_concurrency_slot_until_worker_exits() -> None:
    encoder = FakeEncoder([0.9], sleep_seconds=0.2)
    reranker = make_reranker(
        encoder,
        timeout_seconds=0.05,
        max_concurrency=1,
    )

    with pytest.raises(RerankerProviderError):
        await reranker.rerank("query", [candidate("first")])
    with pytest.raises(RerankerProviderError):
        await reranker.rerank("query", [candidate("second")])
    await asyncio.sleep(0.25)

    assert encoder.max_active == 1


@pytest.mark.asyncio
async def test_model_loading_is_inside_the_wall_clock_timeout() -> None:
    encoder = FakeEncoder([0.9])

    def slow_factory():
        time.sleep(0.2)
        return encoder

    reranker = CrossEncoderReranker(
        timeout_seconds=0.05,
        encoder_factory=slow_factory,
    )
    started = time.perf_counter()

    with pytest.raises(RerankerProviderError):
        await reranker.rerank("query", [candidate("slow load")])

    assert time.perf_counter() - started < 0.15


@pytest.mark.asyncio
async def test_retrieve_evidence_records_cross_encoder_metrics() -> None:
    from app.rag.retrieval import retrieve_evidence

    document_id = uuid.uuid4()
    chunk_row = Chunk(
        id=uuid.uuid4(),
        document_id=document_id,
        chunk_level=ChunkLevel.CHILD,
        ordinal=0,
        text="cross-encoder reranks evidence",
        locator={"page": 1},
        heading_path=["Access"],
        token_count=4,
        chunking_version="legacy",
        content_type="mixed",
        embedding_model="embed-v1",
    )
    document_row = Document(
        id=document_id,
        original_name="policy.md",
        status=DocumentStatus.READY,
    )

    class FakeResult:
        def all(self):
            return [(chunk_row, document_row, 0.2)]

    class FakeSession:
        async def execute(self, *args, **kwargs):
            return FakeResult()

    settings = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        embedding_model_id="embed-v1",
    )
    encoder = FakeEncoder([0.9])
    reranker = make_reranker(encoder)
    metrics = RetrievalMetrics()
    evidence, _ = await retrieve_evidence(
        FakeSession(),
        uuid.uuid4(),
        "query",
        [0.1] * 8,
        settings,
        mode=RetrievalMode.HYBRID_RRF_RERANKED,
        reranker=reranker,
        metrics=metrics,
    )

    assert metrics.reranker_provider == "cross_encoder"
    assert metrics.reranker_model == "test-model"
    assert metrics.reranker_version == "cross_encoder_v1"
    assert metrics.reranker_latency_ms > 0
    assert len(evidence) == 1

import json
import uuid

import pytest

from app.rag import service
from app.rag.generation import GenerationProviderError, GenerationResult
from app.rag.grounding import HistoryMessage
from app.rag.retrieval import Evidence
from app.rag.service import sse_event


def test_sse_event_uses_named_event_and_json_payload() -> None:
    encoded = sse_event("status", {"stage": "retrieving"})
    assert encoded.startswith("event: status\n")
    assert json.loads(encoded.split("data: ", 1)[1]) == {"stage": "retrieving"}


def evidence() -> Evidence:
    return Evidence(
        evidence_id="E1",
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_name="policy.md",
        locator={"page": 1},
        text="Employees may work remotely up to three days per week.",
        retrieval_rank=1,
        retrieval_score=0.02,
        semantic_similarity=0.8,
        lexical_score=0.2,
    )


@pytest.mark.asyncio
async def test_standalone_question_with_history_skips_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_rewrite(_: object) -> GenerationResult:
        raise AssertionError("standalone questions must not be rewritten")

    monkeypatch.setattr(service, "complete_json", unexpected_rewrite)

    query, usage = await service._resolve_query(
        "What is the Tokyo hotel limit?",
        [HistoryMessage(role="user", content="How many remote days are allowed?")],
    )

    assert query == "What is the Tokyo hotel limit?"
    assert usage == {}


@pytest.mark.asyncio
async def test_referential_follow_up_uses_original_question_when_rewrite_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def invalid_rewrite(_: object) -> GenerationResult:
        raise GenerationProviderError("The local generation model returned invalid JSON")

    monkeypatch.setattr(service, "complete_json", invalid_rewrite)

    query, usage = await service._resolve_query(
        "Does that apply during the second week?",
        [HistoryMessage(role="user", content="How many remote days are allowed?")],
    )

    assert query == "Does that apply during the second week?"
    assert usage == {}


@pytest.mark.asyncio
async def test_retries_grounded_answer_rejected_by_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def invalid_generation(_: object) -> GenerationResult:
        return GenerationResult(
            payload={
                "status": "answer",
                "claims": [{"text": "Three days.", "evidence_ids": ["E9"]}],
            },
            usage={"total_tokens": 10},
        )

    async def repaired_generation(_: object) -> GenerationResult:
        return GenerationResult(
            payload={
                "status": "answer",
                "claims": [{"text": "Three days.", "evidence_ids": ["E1"]}],
            },
            usage={"total_tokens": 4},
        )

    monkeypatch.setattr(service, "stream_json", invalid_generation)
    monkeypatch.setattr(service, "complete_json", repaired_generation)

    rendered, usage = await service._generate_grounded_answer(
        "How many remote days are allowed?", [], [evidence()]
    )

    assert rendered.content == "Three days. [1]"
    assert rendered.outcome == "grounded_answer"
    assert usage == {"total_tokens": 14}


@pytest.mark.asyncio
async def test_failed_grounded_answer_repair_returns_safe_completed_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def invalid_generation(_: object) -> GenerationResult:
        return GenerationResult(payload={"answer": "three days"}, usage={})

    async def invalid_repair(_: object) -> GenerationResult:
        raise GenerationProviderError("The local generation model returned invalid JSON")

    monkeypatch.setattr(service, "stream_json", invalid_generation)
    monkeypatch.setattr(service, "complete_json", invalid_repair)

    rendered, usage = await service._generate_grounded_answer(
        "How many remote days are allowed?", [], [evidence()]
    )

    assert rendered.content == service.UNVERIFIABLE_ANSWER
    assert rendered.outcome == "validation_rejected"
    assert usage == {}

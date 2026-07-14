import uuid

import pytest

from app.rag.grounding import GroundingValidationError, parse_rewrite, validate_and_render
from app.rag.retrieval import Evidence


def evidence(identity: str, name: str = "policy.md") -> Evidence:
    return Evidence(
        evidence_id=identity,
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_name=name,
        locator={"page": 2},
        text=f"Evidence text for {identity}",
        retrieval_rank=int(identity[1:]),
        retrieval_score=0.02,
        semantic_similarity=0.8,
        lexical_score=0.2,
    )


def test_renders_server_owned_citation_markers() -> None:
    items = [evidence("E1"), evidence("E2", "handbook.pdf")]
    rendered = validate_and_render(
        {
            "status": "answer",
            "claims": [
                {"text": "Access requires approval.", "evidence_ids": ["E2", "E1"]},
                {"text": "Requests are retained.", "evidence_ids": ["E1"]},
            ],
        },
        items,
    )

    assert rendered.content == "Access requires approval. [1] [2]\n\nRequests are retained. [2]"
    assert [item.evidence_id for item in rendered.used_evidence] == ["E2", "E1"]
    assert rendered.outcome == "grounded_answer"


def test_rejects_unknown_evidence_and_model_written_markers() -> None:
    with pytest.raises(GroundingValidationError):
        validate_and_render(
            {"status": "answer", "claims": [{"text": "Claim", "evidence_ids": ["E9"]}]},
            [evidence("E1")],
        )
    with pytest.raises(GroundingValidationError):
        validate_and_render(
            {"status": "answer", "claims": [{"text": "Claim [E1]", "evidence_ids": ["E1"]}]},
            [evidence("E1")],
        )


def test_conflict_requires_two_retrieved_evidence_items() -> None:
    with pytest.raises(GroundingValidationError):
        validate_and_render(
            {"status": "conflicting", "claims": [{"text": "One view", "evidence_ids": ["E1"]}]},
            [evidence("E1")],
        )


def test_insufficient_response_has_no_citations() -> None:
    rendered = validate_and_render({"status": "insufficient", "claims": []}, [evidence("E1")])
    assert "cannot answer reliably" in rendered.content
    assert rendered.used_evidence == []


def test_follow_up_rewrite_is_bounded_and_validated() -> None:
    assert parse_rewrite({"query": "standalone access policy"}) == "standalone access policy"
    with pytest.raises(GroundingValidationError):
        parse_rewrite({"answer": "not a query"})

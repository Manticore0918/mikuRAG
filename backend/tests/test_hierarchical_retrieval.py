import uuid

import pytest

from app.config import Settings
from app.ingestion.tokenization import ConservativeTokenizer
from app.models import Chunk, ChunkLevel, Document, DocumentStatus
from app.rag.evidence_assembly import (
    apply_adaptive_diversity,
    assemble_evidence_candidates,
    expansion_direction,
    merge_adjacent_candidates,
    suppress_duplicates,
)
from app.rag.reranking import DeterministicReranker
from app.rag.retrieval import _expand_context
from app.rag.retrieval_types import Candidate, RetrievalMetrics


def candidate(
    text: str,
    *,
    document_id: uuid.UUID | None = None,
    parent_id: uuid.UUID | None = None,
    ordinal: int = 0,
    score: float = 1.0,
    start_offset: int | None = None,
    end_offset: int | None = None,
    content_hash: str | None = None,
    content_type: str = "mixed",
) -> Candidate:
    identity = uuid.uuid4()
    return Candidate(
        chunk_id=identity,
        document_id=document_id or uuid.uuid4(),
        document_name="guide.pdf",
        locator={"page": 1},
        text=text,
        parent_chunk_id=parent_id,
        ordinal=ordinal,
        start_page=1,
        end_page=1,
        start_offset=start_offset,
        end_offset=end_offset,
        content_hash=content_hash,
        content_type=content_type,
        semantic_similarity=0.8,
        fused_score=score,
        rerank_score=score,
        source_chunk_ids=(identity,),
    )


def settings(**overrides) -> Settings:
    return Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        hierarchical_retrieval_enabled=True,
        **overrides,
    )


def test_adaptive_diversity_penalizes_repeated_documents_but_not_single_document() -> None:
    first_document = uuid.uuid4()
    second_document = uuid.uuid4()
    first = candidate("first", document_id=first_document, score=1.0)
    repeated = candidate("repeated", document_id=first_document, score=0.99)
    diverse = candidate("diverse", document_id=second_document, score=0.9)

    multi = apply_adaptive_diversity(
        [first, repeated, diverse],
        limit=2,
        document_penalty=0.5,
    )
    single = apply_adaptive_diversity(
        [first, repeated],
        limit=2,
        document_penalty=0.5,
    )

    assert [item.chunk_id for item in multi] == [first.chunk_id, diverse.chunk_id]
    assert [item.chunk_id for item in single] == [first.chunk_id, repeated.chunk_id]


def test_adaptive_diversity_spreads_single_document_across_parents() -> None:
    document_id = uuid.uuid4()
    first_parent = uuid.uuid4()
    second_parent = uuid.uuid4()
    first = candidate(
        "first",
        document_id=document_id,
        parent_id=first_parent,
        score=1.0,
    )
    repeated = candidate(
        "repeated",
        document_id=document_id,
        parent_id=first_parent,
        score=0.95,
    )
    diverse = candidate(
        "diverse",
        document_id=document_id,
        parent_id=second_parent,
        score=0.9,
    )

    selected = apply_adaptive_diversity(
        [first, repeated, diverse],
        limit=2,
        document_penalty=0.5,
    )

    assert [item.chunk_id for item in selected] == [first.chunk_id, diverse.chunk_id]


def test_duplicate_and_high_overlap_candidates_are_suppressed() -> None:
    document_id = uuid.uuid4()
    first = candidate(
        "same",
        document_id=document_id,
        start_offset=0,
        end_offset=100,
        content_hash="a" * 64,
    )
    same_hash = candidate(
        "same",
        document_id=document_id,
        start_offset=0,
        end_offset=100,
        content_hash="a" * 64,
    )
    overlapping = candidate(
        "mostly same",
        document_id=document_id,
        start_offset=5,
        end_offset=95,
    )

    assert suppress_duplicates([first, same_hash, overlapping]) == [first]


@pytest.mark.asyncio
async def test_deterministic_reranker_uses_query_and_heading_terms() -> None:
    document_id = uuid.uuid4()
    weak = candidate("unrelated details", document_id=document_id, score=0.02)
    strong = candidate("Approval is required.", document_id=document_id, score=0.02)
    strong.heading_path = ["Security", "Access"]

    reranked = await DeterministicReranker().rerank(
        "access approval",
        [weak, strong],
    )

    assert reranked[0].chunk_id == strong.chunk_id
    assert reranked[0].rerank_score > reranked[1].rerank_score


def test_adjacent_chunks_merge_and_remove_exact_overlap() -> None:
    document_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    chunks = [
        candidate(
            "alpha beta gamma",
            document_id=document_id,
            parent_id=parent_id,
            ordinal=0,
            score=0.8,
            start_offset=0,
            end_offset=16,
        ),
        candidate(
            "gamma delta epsilon",
            document_id=document_id,
            parent_id=parent_id,
            ordinal=1,
            score=1.0,
            start_offset=11,
            end_offset=30,
        ),
        candidate(
            "epsilon zeta",
            document_id=document_id,
            parent_id=parent_id,
            ordinal=2,
            score=0.7,
            start_offset=24,
            end_offset=36,
        ),
    ]

    merged = merge_adjacent_candidates(
        chunks,
        tokenizer=ConservativeTokenizer(),
        max_tokens=100,
    )

    assert len(merged) == 1
    assert merged[0].text == "alpha beta gamma delta epsilon zeta"
    assert merged[0].start_offset == 0
    assert merged[0].end_offset == 36
    assert len(merged[0].source_chunk_ids) == 3


def test_evidence_assembly_enforces_token_and_item_budgets() -> None:
    candidates = [
        candidate("one", score=1.0),
        candidate("two", score=0.9),
        candidate("three", score=0.8),
    ]
    candidates[0].token_count = 3
    candidates[1].token_count = 3
    candidates[2].token_count = 1

    selected, drops = assemble_evidence_candidates(
        candidates,
        tokenizer=ConservativeTokenizer(),
        max_items=2,
        token_budget=4,
    )

    assert [item.text for item in selected] == ["one", "three"]
    assert drops == {"token_budget": 1}


def test_expansion_direction_selects_only_one_side() -> None:
    parent_id = uuid.uuid4()
    assert expansion_direction(candidate("continues here.", parent_id=parent_id)) == -1
    assert expansion_direction(candidate("Complete statement.", parent_id=parent_id)) == 0
    assert expansion_direction(candidate("Needs more context", parent_id=parent_id)) == 1
    assert (
        expansion_direction(
            candidate(
                "SELECT * FROM chunks",
                parent_id=parent_id,
                content_type="code",
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_multiple_matches_promote_a_bounded_parent() -> None:
    document_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    seeds = [
        candidate(
            "first child.",
            document_id=document_id,
            parent_id=parent_id,
            ordinal=0,
            score=1.0,
        ),
        candidate(
            "second child.",
            document_id=document_id,
            parent_id=parent_id,
            ordinal=1,
            score=0.9,
        ),
    ]
    parent = Chunk(
        id=parent_id,
        document_id=document_id,
        chunk_level=ChunkLevel.PARENT,
        ordinal=0,
        text="Complete parent context.",
        locator={"page": 1},
        heading_path=["Access"],
        token_count=10,
        chunking_version="hierarchical_v1",
        content_type="paragraph",
    )
    document = Document(
        id=document_id,
        original_name="guide.pdf",
        status=DocumentStatus.READY,
    )

    class Result:
        def all(self):
            return [(parent, document)]

    class Session:
        async def execute(self, statement):
            return Result()

    metrics = RetrievalMetrics()
    expanded = await _expand_context(
        Session(),
        uuid.uuid4(),
        seeds,
        settings(),
        ConservativeTokenizer(),
        metrics=metrics,
    )

    assert len(expanded) == 1
    assert expanded[0].chunk_level == ChunkLevel.PARENT
    assert expanded[0].text == "Complete parent context."
    assert set(expanded[0].source_chunk_ids) == {seed.chunk_id for seed in seeds}
    assert metrics.parent_promotion_count == 1


@pytest.mark.asyncio
async def test_incomplete_match_fetches_only_the_helpful_neighbor() -> None:
    document_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    seed = candidate(
        "The requirement continues",
        document_id=document_id,
        parent_id=parent_id,
        ordinal=1,
        score=1.0,
    )
    neighbor = Chunk(
        id=uuid.uuid4(),
        document_id=document_id,
        parent_chunk_id=parent_id,
        chunk_level=ChunkLevel.CHILD,
        ordinal=2,
        text="with the approval conditions.",
        locator={"page": 2},
        start_page=2,
        end_page=2,
        heading_path=["Access"],
        token_count=6,
        chunking_version="hierarchical_v1",
        content_type="paragraph",
        embedding_model="embed-v1",
    )
    document = Document(
        id=document_id,
        original_name="guide.pdf",
        status=DocumentStatus.READY,
    )

    class Result:
        def all(self):
            return [(neighbor, document)]

    class Session:
        async def execute(self, statement):
            return Result()

    metrics = RetrievalMetrics()
    expanded = await _expand_context(
        Session(),
        uuid.uuid4(),
        [seed],
        settings(),
        ConservativeTokenizer(),
        metrics=metrics,
    )

    assert [item.ordinal for item in expanded] == [1, 2]
    assert metrics.neighbor_expansion_count == 1
    assert expanded[1].rerank_score == pytest.approx(0.9)

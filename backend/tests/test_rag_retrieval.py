import uuid

from app.config import Settings
from app.rag.retrieval import Candidate, fuse_rankings, is_sufficient


def candidate(
    document_id: uuid.UUID,
    *,
    semantic: float | None = None,
    lexical: float | None = None,
) -> Candidate:
    identity = uuid.uuid4()
    return Candidate(
        chunk_id=identity,
        document_id=document_id,
        document_name="policy.md",
        locator={"section": "Access"},
        text="Authorized evidence",
        semantic_similarity=semantic,
        lexical_score=lexical,
    )


def settings() -> Settings:
    return Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
    )


def test_rrf_rewards_candidates_present_in_both_rankings() -> None:
    first_document = uuid.uuid4()
    second_document = uuid.uuid4()
    shared = candidate(first_document, semantic=0.8)
    lexical_shared = Candidate(
        **{**shared.__dict__, "semantic_similarity": None, "lexical_score": 0.4}
    )
    semantic_only = candidate(second_document, semantic=0.95)

    ranked = fuse_rankings(
        [semantic_only, shared],
        [lexical_shared],
        rrf_k=60,
        limit=3,
        max_per_document=3,
    )

    assert ranked[0].chunk_id == shared.chunk_id
    assert ranked[0].semantic_similarity == 0.8
    assert ranked[0].lexical_score == 0.4


def test_rrf_limits_chunks_from_one_document() -> None:
    repeated_document = uuid.uuid4()
    other_document = uuid.uuid4()
    semantic = [
        candidate(repeated_document, semantic=0.9),
        candidate(repeated_document, semantic=0.8),
        candidate(other_document, semantic=0.7),
    ]

    ranked = fuse_rankings(
        semantic,
        [],
        rrf_k=60,
        limit=3,
        max_per_document=1,
    )

    assert len(ranked) == 2
    assert {item.document_id for item in ranked} == {repeated_document, other_document}


def test_sufficiency_requires_semantic_or_lexical_threshold() -> None:
    weak = candidate(uuid.uuid4(), semantic=0.1, lexical=0.001)
    strong = candidate(uuid.uuid4(), semantic=0.5)

    assert not is_sufficient([weak], settings())
    assert is_sufficient([weak, strong], settings())

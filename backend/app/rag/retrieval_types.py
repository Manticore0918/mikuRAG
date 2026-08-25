import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Candidate:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    locator: dict[str, Any]
    text: str
    parent_chunk_id: uuid.UUID | None = None
    ordinal: int = 0
    chunk_level: str = "child"
    start_page: int | None = None
    end_page: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    heading_path: list[str] = field(default_factory=list)
    content_type: str = "mixed"
    token_count: int | None = None
    content_hash: str | None = None
    chunking_version: str = "legacy"
    semantic_similarity: float | None = None
    lexical_score: float | None = None
    fused_score: float = 0.0
    rerank_score: float | None = None
    source_chunk_ids: tuple[uuid.UUID, ...] = ()

    @property
    def effective_score(self) -> float:
        return self.rerank_score if self.rerank_score is not None else self.fused_score


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_name: str
    locator: dict[str, Any]
    text: str
    retrieval_rank: int
    retrieval_score: float
    semantic_similarity: float | None
    lexical_score: float | None


@dataclass
class RetrievalMetrics:
    retrieval_duration_ms: float = 0.0
    candidate_generation_ms: float = 0.0
    semantic_query_ms: float = 0.0
    lexical_query_ms: float = 0.0
    reranking_ms: float = 0.0
    semantic_candidate_count: int = 0
    lexical_candidate_count: int = 0
    fused_candidate_count: int = 0
    reranked_candidate_count: int = 0
    neighbor_expansion_count: int = 0
    parent_promotion_count: int = 0
    evidence_token_count: int = 0
    drop_counts: dict[str, int] = field(default_factory=dict)

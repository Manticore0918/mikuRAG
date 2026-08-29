import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class RetrievalMode(enum.StrEnum):
    """Experiment modes select which retrieval legs and post-processing run."""

    VECTOR = "vector"
    FTS_BASELINE = "fts_baseline"
    BM25 = "bm25"
    HYBRID_RRF = "hybrid_rrf"
    HYBRID_RRF_RERANKED = "hybrid_rrf_reranked"


class RewriteStatus(enum.StrEnum):
    """The outcome of query planning for a single turn."""

    UNCHANGED = "unchanged"
    REWRITTEN = "rewritten"
    REWRITE_FAILED = "rewrite_failed"


@dataclass(frozen=True)
class RetrievalFilters:
    """User-selectable metadata filters normalized into one object.

    The authorization scope (knowledge base membership, Ready status, and the
    embedding model) is never part of this object; it is applied separately as
    a mandatory SQL scope inside every retriever.
    """

    document_ids: tuple[uuid.UUID, ...] = ()
    tags: tuple[str, ...] = ()
    source_kinds: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    ingested_after: datetime | None = None
    ingested_before: datetime | None = None

    @classmethod
    def empty(cls) -> "RetrievalFilters":
        return cls()

    def is_empty(self) -> bool:
        return not (
            self.document_ids
            or self.tags
            or self.source_kinds
            or self.languages
            or self.ingested_after is not None
            or self.ingested_before is not None
        )


@dataclass(frozen=True)
class QueryPlan:
    """Typed result of query planning for one turn.

    `effective_query` is what gets embedded and passed to the retriever: the
    rewritten query when rewriting succeeded, otherwise the original query.
    """

    original_query: str
    rewritten_query: str | None = None
    inferred_filters: RetrievalFilters = field(default_factory=RetrievalFilters)
    preserved_identifiers: tuple[str, ...] = ()
    status: RewriteStatus = RewriteStatus.UNCHANGED

    @property
    def effective_query(self) -> str:
        return self.rewritten_query or self.original_query


@dataclass(frozen=True)
class RetrievalPlan:
    """The intent for one retrieval: the effective query, mode, and filters."""

    query_text: str
    mode: RetrievalMode = RetrievalMode.HYBRID_RRF
    filters: RetrievalFilters = field(default_factory=RetrievalFilters)
    query_plan: QueryPlan | None = None


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
    retrieval_mode: str | None = None
    lexical_kind: str | None = None
    bm25_index_available: bool | None = None
    filters_applied: bool = False
    reranker_provider: str | None = None
    reranker_model: str | None = None
    reranker_version: str | None = None
    reranker_latency_ms: float = 0.0
    rewrite_status: str | None = None
    rewrite_latency_ms: float = 0.0
    query_embedding_ms: float = 0.0
    fusion_ms: float = 0.0
    expansion_ms: float = 0.0
    query_embedding_cache_status: str = "disabled"
    retrieval_cache_status: str = "disabled"

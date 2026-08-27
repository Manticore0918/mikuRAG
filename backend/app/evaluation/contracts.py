import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvaluationRunOptions:
    dataset_path: Path
    output_dir: Path
    include_answers: bool = False
    keep_knowledge_base: bool = False
    ingestion_timeout_seconds: int = 300
    poll_seconds: float = 2.0
    run_id: str | None = None
    target_chunking_version: str | None = None
    bootstrap_samples: int = 2000
    bootstrap_seed: int | None = 0


@dataclass(frozen=True)
class EvaluationWorkspace:
    knowledge_base_id: uuid.UUID
    knowledge_base_name: str
    document_ids: dict[str, uuid.UUID]
    storage_keys: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationDocumentRecord:
    corpus_document_id: str
    document_id: str
    original_name: str
    status: str
    ingestion_stage: str
    ingestion_progress: int
    ingestion_attempts: int
    parser_version: str | None
    chunking_version: str | None
    chunk_count: int
    warnings: tuple[dict[str, Any], ...]
    safe_error: str | None
    size_bytes: int = 0
    chunking_config_hash: str | None = None


@dataclass(frozen=True)
class EvaluationEvidenceRecord:
    rank: int
    passage_id: str
    locator_id: str
    document_id: str
    document_name: str
    chunk_id: str
    locator: dict[str, Any]
    text: str
    retrieval_score: float
    semantic_similarity: float | None
    lexical_score: float | None


@dataclass(frozen=True)
class EvaluationAnswerRecord:
    content: str
    outcome: str
    used_passage_ids: tuple[str, ...]
    expected_terms_found: bool
    usage: dict[str, int]
    safe_error: str | None = None


@dataclass(frozen=True)
class EvaluationCaseRecord:
    case_id: str
    category: str
    query: str
    expects_supported_answer: bool
    relevant_passage_ids: tuple[str, ...]
    required_passage_ids: tuple[str, ...]
    expected_citation_pages: tuple[int, ...]
    filters: dict[str, tuple[str, ...]]
    retrieved_passage_ids: tuple[str, ...]
    reranked_passage_ids: tuple[str, ...]
    citation_pages: tuple[int, ...]
    sufficient: bool
    retrieval_passed: bool
    answer_faithful: bool
    retrieval_latency_ms: float
    end_to_end_latency_ms: float
    evidence_tokens: int
    used_summary_path: bool
    retrieval_metrics: dict[str, Any]
    evidence: tuple[EvaluationEvidenceRecord, ...]
    answer: EvaluationAnswerRecord | None = None
    split: str = "train"
    relevance_grades: dict[str, int] = field(default_factory=dict)
    filter_correct: bool | None = None


@dataclass(frozen=True)
class EvaluationRunRecord:
    schema_version: int
    run_id: str
    status: str
    evaluation_set_version: str
    started_at: str
    completed_at: str
    knowledge_base_id: str | None
    knowledge_base_name: str | None
    knowledge_base_cleaned_up: bool
    include_answers: bool
    configuration: dict[str, Any]
    documents: tuple[EvaluationDocumentRecord, ...] = field(default_factory=tuple)
    cases: tuple[EvaluationCaseRecord, ...] = field(default_factory=tuple)
    safe_error: str | None = None
    chunking_config_hash: str | None = None
    ingestion_duration_ms: float | None = None
    embedding_input_count: int = 0
    total_chunk_count: int = 0
    storage_estimate_bytes: int = 0


@dataclass(frozen=True)
class EvaluationArtifactPaths:
    directory: Path
    raw_json: Path
    report_json: Path
    report_markdown: Path


@dataclass(frozen=True)
class EvaluationExecutionResult:
    run: EvaluationRunRecord
    aggregate: dict[str, Any]
    artifacts: EvaluationArtifactPaths

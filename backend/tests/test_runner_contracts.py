"""Tests for the checkpoint-2 runner extensions: filter correctness, the
versioned chunking configuration exposed on runs, and run-option validation.
"""

import uuid
from pathlib import Path

import pytest

from app.config import Settings
from app.evaluation.contracts import (
    EvaluationEvidenceRecord,
    EvaluationRunOptions,
    EvaluationWorkspace,
)
from app.evaluation.datasets import (
    EvaluationCorpusDocument,
    EvaluationCorpusPassage,
    ExecutableEvaluationCase,
    ExecutableEvaluationDataset,
)
from app.evaluation.runner import (
    DatabaseEvaluationRuntime,
    _document_satisfies_filters,
    _filter_correct,
    _retrieval_filters_for_case,
    _validate_options,
)
from app.ingestion.chunkers import build_chunker
from app.rag.retrieval_types import RetrievalMode


def _settings(**overrides) -> Settings:
    values = {
        "session_secret": "s" * 32,
        "encryption_master_key": "e" * 32,
    }
    values.update(overrides)
    return Settings(**values)


def _document(
    document_id: str = "policy-doc",
    *,
    tags: tuple[str, ...] = ("policy",),
    source_kind: str = "markdown",
    language: str | None = "en",
) -> EvaluationCorpusDocument:
    return EvaluationCorpusDocument(
        document_id=document_id,
        passages=(
            EvaluationCorpusPassage(
                passage_id=f"{document_id}-p1",
                locator_id=f"{document_id}-l1",
                locator_match={"section": "s1"},
            ),
        ),
        path=Path("dummy.md"),
        relative_path="dummy.md",
        original_name="dummy.md",
        media_type="text/markdown",
        source_kind=source_kind,
        language=language,
        tags=tags,
        source_uri=None,
        sha256="0" * 64,
        size_bytes=1024,
    )


def _dataset(*documents: EvaluationCorpusDocument) -> ExecutableEvaluationDataset:
    return ExecutableEvaluationDataset(
        schema_version=3,
        version="test_v1",
        description="test",
        license_id="CC0-1.0",
        provenance="synthetic",
        contains_sensitive_data=False,
        review_status="reviewed",
        headline_eligible=False,
        manifest_path=Path("manifest.json"),
        documents=tuple(documents),
        cases=(),
    )


def _case(filters: dict[str, tuple[str, ...]]) -> ExecutableEvaluationCase:
    return ExecutableEvaluationCase(
        case_id="c1",
        category="metadata_filtered",
        query="Question?",
        relevant_passage_ids=(),
        required_passage_ids=(),
        expected_citation_pages=(),
        expected_answer_terms=(),
        expects_supported_answer=True,
        filters=filters,
    )


def _workspace(document_ids: dict[str, uuid.UUID]) -> EvaluationWorkspace:
    return EvaluationWorkspace(
        knowledge_base_id=uuid.uuid4(),
        knowledge_base_name="test",
        document_ids=document_ids,
        storage_keys=(),
    )


def _evidence(document_uuid: uuid.UUID) -> EvaluationEvidenceRecord:
    return EvaluationEvidenceRecord(
        rank=1,
        passage_id="policy-doc-p1",
        locator_id="policy-doc-l1",
        document_id=str(document_uuid),
        document_name="dummy.md",
        chunk_id="chunk-1",
        locator={"section": "s1"},
        text="Policy content.",
        retrieval_score=1.0,
        semantic_similarity=None,
        lexical_score=None,
    )


def test_filter_correct_is_none_when_case_has_no_filters() -> None:
    document = _document()
    workspace = _workspace({"policy-doc": uuid.uuid4()})
    assert _filter_correct(_dataset(document), workspace, _case({}), ()) is None


def test_filter_correct_passes_when_evidence_satisfies_filters() -> None:
    document = _document()
    db_id = uuid.uuid4()
    workspace = _workspace({"policy-doc": db_id})
    case = _case({"tags": ("policy",), "source_kinds": ("markdown",)})
    assert _filter_correct(_dataset(document), workspace, case, (_evidence(db_id),)) is True


def test_case_filters_are_resolved_for_the_production_retriever() -> None:
    db_id = uuid.uuid4()
    workspace = _workspace({"policy-doc": db_id})
    case = _case(
        {
            "document_ids": ("policy-doc",),
            "tags": ("policy",),
            "source_kinds": ("markdown",),
            "languages": ("en",),
        }
    )

    filters = _retrieval_filters_for_case(case, workspace)

    assert filters.document_ids == (db_id,)
    assert filters.tags == ("policy",)
    assert filters.source_kinds == ("markdown",)
    assert filters.languages == ("en",)


def test_filter_correct_passes_vacuously_with_empty_evidence() -> None:
    document = _document()
    workspace = _workspace({"policy-doc": uuid.uuid4()})
    assert _filter_correct(_dataset(document), workspace, _case({"tags": ("policy",)}), ()) is True


def test_filter_correct_fails_when_evidence_violates_tag_filter() -> None:
    document = _document(tags=("hr",))
    db_id = uuid.uuid4()
    workspace = _workspace({"policy-doc": db_id})
    assert (
        _filter_correct(
            _dataset(document),
            workspace,
            _case({"tags": ("policy",)}),
            (_evidence(db_id),),
        )
        is False
    )


def test_filter_correct_fails_when_evidence_violates_document_id_filter() -> None:
    document = _document()
    db_id = uuid.uuid4()
    workspace = _workspace({"policy-doc": db_id})
    assert (
        _filter_correct(
            _dataset(document),
            workspace,
            _case({"document_ids": ("other-doc",)}),
            (_evidence(db_id),),
        )
        is False
    )


def test_filter_correct_fails_for_evidence_outside_the_corpus() -> None:
    document = _document()
    workspace = _workspace({"policy-doc": uuid.uuid4()})
    foreign_db_id = uuid.uuid4()
    assert (
        _filter_correct(
            _dataset(document),
            workspace,
            _case({"tags": ("policy",)}),
            (_evidence(foreign_db_id),),
        )
        is False
    )


def test_document_satisfies_filters_checks_every_dimension() -> None:
    document = _document()
    assert _document_satisfies_filters(document, {"document_ids": ("policy-doc",)}) is True
    assert _document_satisfies_filters(document, {"document_ids": ("other-doc",)}) is False
    assert _document_satisfies_filters(document, {"tags": ("policy",)}) is True
    assert _document_satisfies_filters(document, {"tags": ("policy", "extra")}) is False
    assert _document_satisfies_filters(document, {"source_kinds": ("markdown",)}) is True
    assert _document_satisfies_filters(document, {"source_kinds": ("pdf",)}) is False
    assert _document_satisfies_filters(document, {"languages": ("en",)}) is True
    assert _document_satisfies_filters(document, {"languages": ("de",)}) is False


def test_runtime_public_configuration_exposes_profile_and_config_hash() -> None:
    settings = _settings(chunking_version="token_recursive_v1")
    runtime = DatabaseEvaluationRuntime(settings=settings)

    config = runtime.public_configuration(include_answers=False)

    assert config["chunking_version"] == "token_recursive_v1"
    assert len(config["chunking_config_hash"]) == 64
    chunker = build_chunker(settings, version="token_recursive_v1")
    assert config["chunking_config_hash"] == chunker.config.config_hash


def test_target_chunking_version_overrides_the_configuration_profile() -> None:
    settings = _settings(chunking_version="legacy")
    runtime = DatabaseEvaluationRuntime(
        settings=settings, target_chunking_version="hierarchical_v1"
    )

    config = runtime.public_configuration(include_answers=False)

    assert config["chunking_version"] == "hierarchical_v1"
    assert len(config["chunking_config_hash"]) == 64


def test_options_reject_unknown_chunking_profile(tmp_path: Path) -> None:
    options = EvaluationRunOptions(
        dataset_path=tmp_path / "manifest.json",
        output_dir=tmp_path,
        target_chunking_version="not_a_profile",
    )
    with pytest.raises(ValueError, match="Unsupported chunking profile"):
        _validate_options(options)


def test_options_accept_alias_and_versioned_profiles(tmp_path: Path) -> None:
    for profile in ("legacy", "legacy_char_v1", "token_recursive_v1", "hierarchical_v1"):
        options = EvaluationRunOptions(
            dataset_path=tmp_path / "manifest.json",
            output_dir=tmp_path,
            target_chunking_version=profile,
        )
        _validate_options(options)


def test_runtime_defaults_to_configured_retrieval_mode() -> None:
    settings = _settings()
    runtime = DatabaseEvaluationRuntime(settings=settings)

    assert runtime.retrieval_mode == settings.retrieval_mode
    assert runtime.effective_settings is settings
    assert runtime.query_planning is True


def test_runtime_accepts_retrieval_mode_and_reranker_overrides() -> None:
    settings = _settings(retrieval_mode="hybrid_rrf")
    runtime = DatabaseEvaluationRuntime(
        settings=settings,
        retrieval_mode="bm25",
        reranker_provider="cross_encoder",
        query_planning=False,
    )

    assert runtime.retrieval_mode == RetrievalMode.BM25
    assert runtime.reranker.provider_name == "cross_encoder"
    assert runtime.query_planning is False


def test_runtime_bm25_hybrid_enabled_overrides_settings_copy() -> None:
    settings = _settings(bm25_hybrid_enabled=False)
    runtime = DatabaseEvaluationRuntime(
        settings=settings,
        bm25_hybrid_enabled=True,
    )

    assert runtime.effective_settings is not settings
    assert runtime.effective_settings.bm25_hybrid_enabled is True
    assert settings.bm25_hybrid_enabled is False


def test_runtime_public_configuration_exposes_retrieval_keys() -> None:
    settings = _settings(
        retrieval_mode="hybrid_rrf_reranked",
        retrieval_rrf_k=40,
        retrieval_rrf_semantic_weight=1.5,
        retrieval_rrf_lexical_weight=0.8,
    )
    runtime = DatabaseEvaluationRuntime(
        settings=settings,
        reranker_provider="cross_encoder",
        bm25_hybrid_enabled=True,
    )

    config = runtime.public_configuration(include_answers=False)

    assert config["retrieval_mode"] == "hybrid_rrf_reranked"
    assert config["reranker_provider"] == "cross_encoder"
    assert config["bm25_hybrid_enabled"] is True
    assert config["query_planning"] is True
    assert config["retrieval_rrf_k"] == 40
    assert config["retrieval_rrf_semantic_weight"] == 1.5
    assert config["retrieval_rrf_lexical_weight"] == 0.8
    assert config["reranker_warmup_status"] == "pending"
    assert config["reranker_warmup_ms"] is None
    assert config["retrieval_latency_scope"] == "pending"


@pytest.mark.asyncio
async def test_runtime_prewarms_reranker_outside_timed_cases() -> None:
    class RecordingReranker:
        provider_name = "cross_encoder"

        def __init__(self) -> None:
            self.calls = 0

        async def warmup(self) -> None:
            self.calls += 1

    runtime = DatabaseEvaluationRuntime(
        settings=_settings(retrieval_mode="hybrid_rrf_reranked"),
        reranker_provider="cross_encoder",
    )
    reranker = RecordingReranker()
    runtime.reranker = reranker

    await runtime.warmup()
    config = runtime.public_configuration(include_answers=False)

    assert reranker.calls == 1
    assert config["reranker_warmup_status"] == "completed"
    assert config["reranker_warmup_ms"] >= 0
    assert config["retrieval_latency_scope"] == "steady_state_after_reranker_warmup"


def test_options_reject_unknown_retrieval_mode(tmp_path: Path) -> None:
    options = EvaluationRunOptions(
        dataset_path=tmp_path / "manifest.json",
        output_dir=tmp_path,
        retrieval_mode="not_a_mode",
    )
    with pytest.raises(ValueError, match="Unsupported retrieval mode"):
        _validate_options(options)


def test_options_reject_unknown_reranker_provider(tmp_path: Path) -> None:
    options = EvaluationRunOptions(
        dataset_path=tmp_path / "manifest.json",
        output_dir=tmp_path,
        reranker_provider="not_a_provider",
    )
    with pytest.raises(ValueError, match="Unsupported reranker provider"):
        _validate_options(options)


def test_options_accept_every_experiment_mode_and_reranker(tmp_path: Path) -> None:
    for mode in (
        "vector",
        "fts_baseline",
        "bm25",
        "hybrid_rrf",
        "hybrid_rrf_reranked",
    ):
        options = EvaluationRunOptions(
            dataset_path=tmp_path / "manifest.json",
            output_dir=tmp_path,
            retrieval_mode=mode,
        )
        _validate_options(options)
    for provider in ("deterministic", "cross_encoder"):
        options = EvaluationRunOptions(
            dataset_path=tmp_path / "manifest.json",
            output_dir=tmp_path,
            reranker_provider=provider,
        )
        _validate_options(options)

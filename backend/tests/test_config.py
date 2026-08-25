from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings

ARCHITECTURE_ENV_KEYS = {
    "MIKURAG_CHUNKING_VERSION",
    "MIKURAG_CHUNK_TOKENIZER",
    "MIKURAG_CHUNK_TARGET_CHARACTERS",
    "MIKURAG_CHUNK_OVERLAP_CHARACTERS",
    "MIKURAG_CHILD_MIN_TOKENS",
    "MIKURAG_CHILD_TARGET_TOKENS",
    "MIKURAG_CHILD_MAX_TOKENS",
    "MIKURAG_CHILD_OVERLAP_TOKENS",
    "MIKURAG_PARENT_TARGET_TOKENS",
    "MIKURAG_PARENT_MAX_TOKENS",
    "MIKURAG_HIERARCHICAL_RETRIEVAL_ENABLED",
    "MIKURAG_RETRIEVAL_SEMANTIC_CANDIDATES",
    "MIKURAG_RETRIEVAL_LEXICAL_CANDIDATES",
    "MIKURAG_RETRIEVAL_RERANK_CANDIDATES",
    "MIKURAG_RETRIEVAL_EVIDENCE_TOKEN_BUDGET",
    "MIKURAG_RETRIEVAL_MAX_MERGED_PASSAGE_TOKENS",
    "MIKURAG_RETRIEVAL_NEIGHBOR_EXPANSION_COUNT",
    "MIKURAG_SUMMARY_GENERATION_ENABLED",
    "MIKURAG_ACCEPTANCE_MIN_QUALITY_IMPROVEMENT",
    "MIKURAG_ACCEPTANCE_RETRIEVAL_P95_TARGET_MS",
    "MIKURAG_WORKER_MEMORY_LIMIT_BYTES",
    "MIKURAG_REINDEX_MAX_ATTEMPTS",
    "MIKURAG_REINDEX_BATCH_DELAY_SECONDS",
}


def test_rejects_non_postgres_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url="sqlite:///test.db",
            session_secret="s" * 32,
            encryption_master_key="e" * 32,
        )


def test_accepts_expected_service_urls() -> None:
    settings = Settings(
        environment="development",
        database_url="postgresql+asyncpg://user:pass@postgres:5432/mikurag",
        redis_url="redis://redis:6379/0",
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
    )
    assert settings.environment == "development"


def test_architecture_settings_are_wired_into_deployment_templates() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    environment_template = (repository_root / ".env.example").read_text(encoding="utf-8")
    compose = (repository_root / "compose.yaml").read_text(encoding="utf-8")

    for key in ARCHITECTURE_ENV_KEYS:
        assert f"{key}=" in environment_template
        assert f"{key}:" in compose


def test_rejects_insecure_embedding_endpoint() -> None:
    with pytest.raises(ValidationError):
        Settings(
            session_secret="s" * 32,
            encryption_master_key="e" * 32,
            embedding_endpoint="http://provider.example/embed",
        )


def test_rejects_chunk_overlap_larger_than_target() -> None:
    with pytest.raises(ValidationError):
        Settings(
            session_secret="s" * 32,
            encryption_master_key="e" * 32,
            chunk_target_characters=500,
            chunk_overlap_characters=500,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("child_min_tokens", 600),
        ("child_overlap_tokens", 200),
        ("parent_target_tokens", 750),
        ("parent_max_tokens", 1_500),
    ],
)
def test_rejects_invalid_hierarchical_chunk_settings(field: str, value: int) -> None:
    values = {
        "session_secret": "s" * 32,
        "encryption_master_key": "e" * 32,
        field: value,
    }
    with pytest.raises(ValidationError):
        Settings(**values)


def test_hierarchical_chunking_is_opt_in() -> None:
    legacy = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
    )
    hierarchical = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        chunking_version="hierarchical_v1",
    )

    assert legacy.chunking_version == "legacy"
    assert hierarchical.chunking_version == "hierarchical_v1"


def test_retrieval_budget_must_fit_chunk_and_item_limits() -> None:
    with pytest.raises(ValidationError):
        Settings(
            session_secret="s" * 32,
            encryption_master_key="e" * 32,
            retrieval_evidence_token_budget=500,
        )
    with pytest.raises(ValidationError):
        Settings(
            session_secret="s" * 32,
            encryption_master_key="e" * 32,
            retrieval_rerank_candidates=4,
            retrieval_evidence_limit=8,
        )
    with pytest.raises(ValidationError):
        Settings(
            session_secret="s" * 32,
            encryption_master_key="e" * 32,
            retrieval_evidence_token_budget=1_000,
            retrieval_max_merged_passage_tokens=1_001,
        )


def test_hierarchical_configuration_accepts_valid_boundary_values() -> None:
    configured = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        chunking_version="hierarchical_v1",
        child_min_tokens=500,
        child_target_tokens=500,
        child_max_tokens=500,
        child_overlap_tokens=499,
        parent_target_tokens=501,
        parent_max_tokens=501,
        retrieval_evidence_token_budget=500,
        retrieval_max_merged_passage_tokens=500,
    )

    assert configured.child_min_tokens == configured.child_target_tokens
    assert configured.child_target_tokens == configured.child_max_tokens
    assert configured.parent_target_tokens > configured.child_max_tokens


def test_summary_generation_requires_hierarchical_chunking() -> None:
    with pytest.raises(ValidationError):
        Settings(
            session_secret="s" * 32,
            encryption_master_key="e" * 32,
            summary_generation_enabled=True,
            chunking_version="legacy",
        )


def test_summary_context_budget_must_fit_one_summary() -> None:
    with pytest.raises(ValidationError):
        Settings(
            session_secret="s" * 32,
            encryption_master_key="e" * 32,
            summary_max_tokens=500,
            summary_context_token_budget=400,
        )


def test_upload_part_size_must_fit_within_document_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(
            session_secret="s" * 32,
            encryption_master_key="e" * 32,
            max_upload_bytes=1_048_576,
            upload_part_bytes=5_242_880,
        )


def test_empty_embedding_key_is_treated_as_unconfigured() -> None:
    settings = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        embedding_api_key="",
    )
    assert settings.embedding_api_key is None


def test_generation_endpoint_accepts_local_http_and_rejects_remote_http() -> None:
    local = Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        generation_base_url="http://localhost:11434/v1/",
    )
    assert local.generation_base_url == "http://localhost:11434/v1"
    with pytest.raises(ValidationError):
        Settings(
            session_secret="s" * 32,
            encryption_master_key="e" * 32,
            generation_base_url="http://models.example/v1",
        )

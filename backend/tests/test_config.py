import pytest
from pydantic import ValidationError

from app.config import Settings


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

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MIKURAG_",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+asyncpg://mikurag:mikurag@localhost:5432/mikurag"
    redis_url: str = "redis://localhost:6379/0"
    upload_dir: Path = Path("./uploads")
    session_secret: str = Field(min_length=32)
    encryption_master_key: str = Field(min_length=32)
    cors_origins: list[str] = ["http://localhost:5173"]
    session_max_age_seconds: int = Field(default=28_800, ge=300, le=604_800)
    login_attempt_limit: int = Field(default=5, ge=1, le=20)
    login_attempt_window_seconds: int = Field(default=300, ge=60, le=3_600)
    max_upload_bytes: int = Field(default=52_428_800, ge=1_048_576, le=104_857_600)
    upload_part_bytes: int = Field(default=5_242_880, ge=1_048_576, le=10_485_760)
    upload_session_ttl_seconds: int = Field(default=86_400, ge=3_600, le=604_800)
    max_active_upload_sessions: int = Field(default=20, ge=1, le=100)
    max_document_pages: int = Field(default=500, ge=1, le=2_000)
    chunking_version: Literal[
        "legacy", "legacy_char_v1", "token_recursive_v1", "hierarchical_v1"
    ] = "legacy"
    chunk_tokenizer: Literal["conservative_v1"] = "conservative_v1"
    child_min_tokens: int = Field(default=200, ge=1, le=4_000)
    child_target_tokens: int = Field(default=500, ge=1, le=8_000)
    child_max_tokens: int = Field(default=750, ge=1, le=16_000)
    child_overlap_tokens: int = Field(default=60, ge=0, le=4_000)
    parent_target_tokens: int = Field(default=2_000, ge=1, le=32_000)
    parent_max_tokens: int = Field(default=3_000, ge=1, le=64_000)
    summary_generation_enabled: bool = False
    summary_prompt_version: str = Field(
        default="hierarchical_summary_v1",
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_.-]+$",
    )
    summary_max_tokens: int = Field(default=600, ge=64, le=4_000)
    summary_reduce_batch_size: int = Field(default=8, ge=2, le=32)
    summary_retrieval_limit: int = Field(default=8, ge=1, le=50)
    summary_context_token_budget: int = Field(default=4_000, ge=128, le=32_000)
    chunk_target_characters: int = Field(default=800, ge=500, le=8_000)
    chunk_overlap_characters: int = Field(default=100, ge=0, le=1_000)
    max_document_chunks: int = Field(default=5_000, ge=100, le=20_000)
    max_document_tokens: int = Field(default=2_000_000, ge=1_000, le=20_000_000)
    embedding_endpoint: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/embeddings/"
        "multimodal-embedding/multimodal-embedding"
    )
    embedding_model_id: str = "tongyi-embedding-vision-flash-2026-03-06"
    embedding_api_key: SecretStr | None = None
    embedding_batch_size: int = Field(default=8, ge=1, le=20)
    embedding_timeout_seconds: float = Field(default=60, ge=5, le=300)
    generation_base_url: str = "http://host.docker.internal:11434/v1"
    generation_model_id: str = "DeepSeek-R1-Distill-Qwen-7B"
    generation_api_key: SecretStr | None = None
    generation_timeout_seconds: float = Field(default=300, ge=10, le=900)
    generation_max_tokens: int = Field(default=1_200, ge=128, le=8_192)
    recent_history_messages: int = Field(default=8, ge=0, le=20)
    hierarchical_retrieval_enabled: bool = False
    retrieval_semantic_candidates: int = Field(default=50, ge=5, le=100)
    retrieval_lexical_candidates: int = Field(default=50, ge=5, le=100)
    retrieval_rerank_candidates: int = Field(default=20, ge=1, le=100)
    retrieval_evidence_limit: int = Field(default=8, ge=1, le=20)
    retrieval_max_chunks_per_document: int = Field(default=3, ge=1, le=10)
    retrieval_evidence_token_budget: int = Field(default=6_000, ge=128, le=64_000)
    retrieval_max_merged_passage_tokens: int = Field(default=1_500, ge=64, le=16_000)
    retrieval_neighbor_expansion_count: int = Field(default=1, ge=0, le=3)
    retrieval_document_diversity_penalty: float = Field(default=0.2, ge=0, le=1)
    retrieval_rrf_k: int = Field(default=60, ge=1, le=200)
    retrieval_min_semantic_similarity: float = Field(default=0.28, ge=-1, le=1)
    retrieval_min_lexical_score: float = Field(default=0.01, ge=0, le=10)
    acceptance_min_quality_improvement: float = Field(default=0.02, ge=0, le=1)
    acceptance_retrieval_p95_target_ms: float = Field(
        default=1_500,
        ge=10,
        le=60_000,
    )
    worker_memory_limit_bytes: int = Field(
        default=1_073_741_824,
        ge=67_108_864,
        le=34_359_738_368,
    )
    reindex_max_attempts: int = Field(default=3, ge=1, le=10)
    reindex_batch_delay_seconds: float = Field(default=2, ge=0, le=300)
    reindex_stale_after_seconds: int = Field(default=900, ge=60, le=86_400)
    ingestion_stale_after_seconds: int = Field(default=900, ge=60, le=86_400)
    ingestion_busy_retry_seconds: int = Field(default=30, ge=5, le=300)
    stale_turn_seconds: int = Field(default=900, ge=60, le=86_400)

    @field_validator("database_url")
    @classmethod
    def require_async_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must use postgresql+asyncpg")
        return value

    @field_validator("redis_url")
    @classmethod
    def require_redis(cls, value: str) -> str:
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("redis_url must use redis:// or rediss://")
        return value

    @field_validator("embedding_endpoint")
    @classmethod
    def require_embedding_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("embedding_endpoint must use https://")
        return value

    @field_validator("embedding_api_key", mode="before")
    @classmethod
    def empty_embedding_key_is_unconfigured(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("generation_api_key", mode="before")
    @classmethod
    def empty_generation_key_is_unconfigured(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("generation_base_url")
    @classmethod
    def require_safe_generation_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme == "https":
            return value.rstrip("/")
        local_hosts = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "ollama"}
        if parsed.scheme == "http" and parsed.hostname in local_hosts:
            return value.rstrip("/")
        raise ValueError("generation_base_url must use HTTPS outside the local Installation")

    @model_validator(mode="after")
    def require_smaller_chunk_overlap(self) -> "Settings":
        if self.chunk_overlap_characters >= self.chunk_target_characters:
            raise ValueError("chunk_overlap_characters must be smaller than the chunk target")
        if not self.child_min_tokens <= self.child_target_tokens <= self.child_max_tokens:
            raise ValueError("child token settings must satisfy minimum <= target <= maximum")
        if self.child_overlap_tokens >= self.child_min_tokens:
            raise ValueError("child_overlap_tokens must be smaller than child_min_tokens")
        if self.parent_target_tokens <= self.child_max_tokens:
            raise ValueError("parent_target_tokens must exceed child_max_tokens")
        if self.parent_target_tokens > self.parent_max_tokens:
            raise ValueError("parent_target_tokens cannot exceed parent_max_tokens")
        if self.summary_generation_enabled and self.chunking_version != "hierarchical_v1":
            raise ValueError("summary generation requires hierarchical chunking")
        if self.retrieval_rerank_candidates < self.retrieval_evidence_limit:
            raise ValueError("retrieval_rerank_candidates must cover the evidence item limit")
        if self.retrieval_evidence_token_budget < self.child_max_tokens:
            raise ValueError("the evidence token budget must fit one maximum-sized child")
        if self.retrieval_max_merged_passage_tokens < self.child_max_tokens:
            raise ValueError("the merged passage maximum must fit one maximum-sized child")
        if (
            self.retrieval_max_merged_passage_tokens
            > self.retrieval_evidence_token_budget
        ):
            raise ValueError("the merged passage maximum cannot exceed the evidence budget")
        if self.summary_context_token_budget < self.summary_max_tokens:
            raise ValueError("the summary context budget must fit one maximum-sized summary")
        if self.upload_part_bytes > self.max_upload_bytes:
            raise ValueError("upload_part_bytes cannot exceed max_upload_bytes")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

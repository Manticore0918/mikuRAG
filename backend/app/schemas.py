import uuid
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ingestion.provenance import validate_source_metadata, validate_tags
from app.rag.citations import LocatorValue


class UsernamePassword(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=12, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class LoginRequest(UsernamePassword):
    pass


class UserCreate(UsernamePassword):
    is_administrator: bool = False


class UserUpdate(BaseModel):
    is_enabled: bool | None = None
    is_administrator: bool | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=12, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    is_administrator: bool
    is_enabled: bool
    created_at: datetime


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2_000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class KnowledgeBaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime


class AccessGrantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    knowledge_base_id: uuid.UUID


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    original_name: str
    media_type: str
    size_bytes: int
    page_count: int | None
    status: str
    safe_error: str | None
    parser_version: str | None
    chunking_version: str | None
    source_kind: str
    language: str | None
    tags: list[str]
    source_uri: str | None
    source_path: str | None
    source_metadata: dict[str, Any]
    ingestion_stage: str
    ingestion_progress: int
    ingestion_attempts: int
    ingestion_warnings: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


class ReindexJobCreate(BaseModel):
    target_chunking_version: str = Field(pattern=r"^(legacy|hierarchical_v1)$")
    selection_mode: str = Field(pattern=r"^(canary|all)$")
    canary_percentage: int = Field(default=10, ge=1, le=100)
    batch_size: int = Field(default=10, ge=1, le=100)
    knowledge_base_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def require_full_percentage_for_all(self) -> "ReindexJobCreate":
        if self.selection_mode == "all" and self.canary_percentage != 100:
            raise ValueError("All-document selection requires canary_percentage=100")
        return self


class ReindexRollbackCreate(BaseModel):
    batch_size: int = Field(default=10, ge=1, le=100)


class ReindexJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    target_chunking_version: str
    selection_mode: str
    canary_percentage: int
    batch_size: int
    status: str
    knowledge_base_id: uuid.UUID | None
    created_by_id: uuid.UUID | None
    total_documents: int
    completed_documents: int
    failed_documents: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class ChunkingRolloutStatusRead(BaseModel):
    configured_phase: str
    chunking_version: str
    hierarchical_retrieval_enabled: bool
    summary_generation_enabled: bool
    document_version_counts: dict[str, int]
    active_jobs: list[ReindexJobRead]


class UploadSessionCreate(BaseModel):
    original_name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    language: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9+.#_-]+$")
    tags: list[str] = Field(default_factory=list)
    source_uri: str | None = Field(default=None, max_length=2_048)
    source_path: str | None = Field(default=None, max_length=1_024)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("original_name")
    @classmethod
    def clean_original_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("sha256")
    @classmethod
    def normalize_sha256(cls, value: str) -> str:
        return value.lower()

    @field_validator("language", mode="before")
    @classmethod
    def clean_language(cls, value: str | None) -> str | None:
        return value.strip().casefold() if value is not None else None

    @field_validator("tags", mode="before")
    @classmethod
    def clean_tags(cls, value: object) -> list[str]:
        return validate_tags(value)

    @field_validator("source_uri")
    @classmethod
    def clean_source_uri(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source_uri must be an absolute HTTP or HTTPS URL")
        return cleaned

    @field_validator("source_path")
    @classmethod
    def clean_source_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().replace("\\", "/")
        path = PurePosixPath(cleaned)
        if (
            not cleaned
            or path.is_absolute()
            or ".." in path.parts
            or (path.parts and path.parts[0].endswith(":"))
            or any(not character.isprintable() for character in cleaned)
        ):
            raise ValueError("source_path must be a safe repository-relative path")
        return str(path)

    @field_validator("metadata", mode="before")
    @classmethod
    def clean_metadata(cls, value: object) -> dict[str, Any]:
        return validate_source_metadata(value)


class UploadSessionRead(BaseModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    initiated_by_id: uuid.UUID | None
    initiated_by_username: str | None
    original_name: str
    source_kind: str
    language: str | None
    tags: list[str]
    source_uri: str | None
    source_path: str | None
    source_metadata: dict[str, Any]
    declared_sha256: str
    total_bytes: int
    received_bytes: int
    part_size_bytes: int
    status: str
    safe_error: str | None
    resulting_document_id: uuid.UUID | None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class UploadPartRead(BaseModel):
    next_offset: int
    expires_at: datetime


class ConversationCreate(BaseModel):
    knowledge_base_id: uuid.UUID


class ConversationRead(BaseModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    knowledge_base_name: str
    title: str
    created_at: datetime
    updated_at: datetime


class CitationRead(BaseModel):
    id: uuid.UUID
    document_name: str
    locator: dict[str, LocatorValue]
    excerpt: str
    retrieval_rank: int
    retrieval_score: float | None
    source_available: bool
    source_url: str | None


class MessageRead(BaseModel):
    id: uuid.UUID
    sequence: int
    role: str
    status: str
    content: str
    created_at: datetime
    citations: list[CitationRead] = Field(default_factory=list)


class ConversationDetail(ConversationRead):
    messages: list[MessageRead]


class TurnCreate(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        return value.strip()

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    created_at: datetime
    updated_at: datetime


class UploadSessionCreate(BaseModel):
    original_name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")

    @field_validator("original_name")
    @classmethod
    def clean_original_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("sha256")
    @classmethod
    def normalize_sha256(cls, value: str) -> str:
        return value.lower()


class UploadSessionRead(BaseModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    initiated_by_id: uuid.UUID | None
    initiated_by_username: str | None
    original_name: str
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
    locator: dict[str, int | str]
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

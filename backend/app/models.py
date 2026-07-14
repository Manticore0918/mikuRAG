import enum
import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DocumentStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"


class MessageRole(enum.StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(enum.StrEnum):
    STREAMING = "streaming"
    COMPLETE = "complete"
    FAILED = "failed"


class ProviderKind(enum.StrEnum):
    EMBEDDING = "embedding"
    GENERATION = "generation"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_administrator: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (Index("users_username_lower_uq", func.lower(username), unique=True),)


class KnowledgeBase(TimestampMixin, Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)


class KnowledgeBaseAccess(Base):
    __tablename__ = "knowledge_base_access"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Document(TimestampMixin, Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=DocumentStatus.PENDING)
    safe_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "sha256", name="documents_kb_sha256_uq"),
        Index("documents_kb_status_idx", "knowledge_base_id", "status"),
    )


class Chunk(TimestampMixin, Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    search_vector: Mapped[Any | None] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768))
    embedding_model: Mapped[str] = mapped_column(String(120), nullable=False)

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="chunks_document_ordinal_uq"),
        Index("chunks_search_vector_gin", "search_vector", postgresql_using="gin"),
        Index(
            "chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="messages_conversation_sequence_uq"),
        Index(
            "messages_one_streaming_assistant_uq",
            "conversation_id",
            unique=True,
            postgresql_where=(role == MessageRole.ASSISTANT) & (status == MessageStatus.STREAMING),
        ),
    )


class Citation(Base):
    __tablename__ = "citations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )
    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieval_score: Mapped[float | None]


class ProviderConfiguration(TimestampMixin, Base):
    __tablename__ = "provider_configurations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    model_id: Mapped[str] = mapped_column(String(160), nullable=False)
    encrypted_api_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    is_validated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

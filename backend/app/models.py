import enum
import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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


class SourceKind(enum.StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    TEXT = "text"
    MARKDOWN = "markdown"
    HTML = "html"
    CODE = "code"


class UploadSessionStatus(enum.StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    FAILED = "failed"


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


class ChunkLevel(enum.StrEnum):
    CHILD = "child"
    PARENT = "parent"
    SECTION_SUMMARY = "section_summary"
    DOCUMENT_SUMMARY = "document_summary"


class ChunkContentType(enum.StrEnum):
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    CODE = "code"
    MIXED = "mixed"
    SUMMARY = "summary"


class ReindexJobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReindexItemStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReindexSelection(enum.StrEnum):
    CANARY = "canary"
    ALL = "all"


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
    parser_version: Mapped[str | None] = mapped_column(String(64))
    chunking_version: Mapped[str | None] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SourceKind.TEXT, server_default=SourceKind.TEXT
    )
    language: Mapped[str | None] = mapped_column(String(64))
    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    source_uri: Mapped[str | None] = mapped_column(String(2_048))
    source_path: Mapped[str | None] = mapped_column(String(1_024))
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    ingestion_stage: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued"
    )
    ingestion_progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    ingestion_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    ingestion_warnings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "sha256", name="documents_kb_sha256_uq"),
        Index("documents_kb_status_idx", "knowledge_base_id", "status"),
        CheckConstraint(
            "source_kind IN ('pdf', 'docx', 'text', 'markdown', 'html', 'code')",
            name="documents_source_kind_ck",
        ),
        CheckConstraint(
            "ingestion_progress BETWEEN 0 AND 100",
            name="documents_ingestion_progress_ck",
        ),
        CheckConstraint("ingestion_attempts >= 0", name="documents_ingestion_attempts_ck"),
        CheckConstraint("jsonb_typeof(tags) = 'array'", name="documents_tags_array_ck"),
        CheckConstraint(
            "jsonb_typeof(source_metadata) = 'object'",
            name="documents_source_metadata_object_ck",
        ),
        CheckConstraint(
            "jsonb_typeof(ingestion_warnings) = 'array'",
            name="documents_ingestion_warnings_array_ck",
        ),
    )


class ReindexJob(TimestampMixin, Base):
    __tablename__ = "reindex_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    target_chunking_version: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    canary_percentage: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ReindexJobStatus.QUEUED
    )
    knowledge_base_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    total_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "target_chunking_version IN ('legacy', 'hierarchical_v1')",
            name="reindex_jobs_target_version_ck",
        ),
        CheckConstraint(
            "selection_mode IN ('canary', 'all')",
            name="reindex_jobs_selection_ck",
        ),
        CheckConstraint(
            "canary_percentage BETWEEN 1 AND 100",
            name="reindex_jobs_canary_percentage_ck",
        ),
        CheckConstraint(
            "batch_size BETWEEN 1 AND 100",
            name="reindex_jobs_batch_size_ck",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'paused', 'completed', 'failed', "
            "'cancelled')",
            name="reindex_jobs_status_ck",
        ),
        CheckConstraint(
            "total_documents >= 0 AND completed_documents >= 0 "
            "AND failed_documents >= 0 "
            "AND completed_documents + failed_documents <= total_documents",
            name="reindex_jobs_counts_ck",
        ),
        Index("reindex_jobs_status_created_idx", "status", "created_at"),
    )


class ReindexItem(Base):
    __tablename__ = "reindex_items"

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reindex_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ReindexItemStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    safe_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')",
            name="reindex_items_status_ck",
        ),
        CheckConstraint("attempts >= 0", name="reindex_items_attempts_ck"),
        Index("reindex_items_job_status_idx", "job_id", "status"),
    )


class UploadSession(TimestampMixin, Base):
    __tablename__ = "upload_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    initiated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    suffix: Mapped[str] = mapped_column(String(16), nullable=False)
    source_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SourceKind.TEXT, server_default=SourceKind.TEXT
    )
    language: Mapped[str | None] = mapped_column(String(64))
    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    source_uri: Mapped[str | None] = mapped_column(String(2_048))
    source_path: Mapped[str | None] = mapped_column(String(1_024))
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    declared_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    received_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    part_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    temporary_storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    final_storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UploadSessionStatus.OPEN
    )
    safe_error: Mapped[str | None] = mapped_column(Text)
    resulting_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), unique=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "received_bytes >= 0 AND received_bytes <= total_bytes",
            name="upload_sessions_received_bytes_ck",
        ),
        CheckConstraint("total_bytes > 0", name="upload_sessions_total_bytes_ck"),
        CheckConstraint("part_size_bytes > 0", name="upload_sessions_part_size_bytes_ck"),
        CheckConstraint(
            "source_kind IN ('pdf', 'docx', 'text', 'markdown', 'html', 'code')",
            name="upload_sessions_source_kind_ck",
        ),
        CheckConstraint("jsonb_typeof(tags) = 'array'", name="upload_sessions_tags_array_ck"),
        CheckConstraint(
            "jsonb_typeof(source_metadata) = 'object'",
            name="upload_sessions_source_metadata_object_ck",
        ),
        Index("upload_sessions_kb_status_idx", "knowledge_base_id", "status"),
        Index("upload_sessions_expires_at_idx", "expires_at"),
        Index(
            "upload_sessions_open_kb_sha256_uq",
            "knowledge_base_id",
            "declared_sha256",
            unique=True,
            postgresql_where=status == UploadSessionStatus.OPEN,
        ),
    )


class UploadPartReceipt(Base):
    __tablename__ = "upload_part_receipts"

    upload_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("upload_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    offset_bytes: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    length_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("offset_bytes >= 0", name="upload_part_receipts_offset_ck"),
        CheckConstraint("length_bytes > 0", name="upload_part_receipts_length_ck"),
    )


class Chunk(TimestampMixin, Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), index=True
    )
    chunk_level: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ChunkLevel.CHILD, server_default=ChunkLevel.CHILD
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    start_page: Mapped[int | None] = mapped_column(Integer)
    end_page: Mapped[int | None] = mapped_column(Integer)
    start_offset: Mapped[int | None] = mapped_column(BigInteger)
    end_offset: Mapped[int | None] = mapped_column(BigInteger)
    heading_path: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    content_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ChunkContentType.MIXED,
        server_default=ChunkContentType.MIXED,
    )
    token_count: Mapped[int | None] = mapped_column(Integer)
    chunking_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="legacy", server_default="legacy"
    )
    chunking_config_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    content_hash: Mapped[str | None] = mapped_column(String(64))
    search_vector: Mapped[Any | None] = mapped_column(TSVECTOR)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768))
    embedding_model: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_level",
            "ordinal",
            name="chunks_document_level_ordinal_uq",
        ),
        CheckConstraint(
            "chunk_level IN ('child', 'parent', 'section_summary', 'document_summary')",
            name="chunks_level_ck",
        ),
        CheckConstraint(
            "content_type IN ('paragraph', 'list', 'table', 'code', 'mixed', 'summary')",
            name="chunks_content_type_ck",
        ),
        CheckConstraint(
            "parent_chunk_id IS NULL OR parent_chunk_id <> id",
            name="chunks_parent_not_self_ck",
        ),
        CheckConstraint(
            "start_page IS NULL OR start_page > 0",
            name="chunks_start_page_ck",
        ),
        CheckConstraint(
            "end_page IS NULL OR end_page > 0",
            name="chunks_end_page_ck",
        ),
        CheckConstraint(
            "start_page IS NULL OR end_page IS NULL OR end_page >= start_page",
            name="chunks_page_range_ck",
        ),
        CheckConstraint(
            "start_offset IS NULL OR start_offset >= 0",
            name="chunks_start_offset_ck",
        ),
        CheckConstraint(
            "end_offset IS NULL OR end_offset >= 0",
            name="chunks_end_offset_ck",
        ),
        CheckConstraint(
            "start_offset IS NULL OR end_offset IS NULL OR end_offset >= start_offset",
            name="chunks_offset_range_ck",
        ),
        CheckConstraint(
            "token_count IS NULL OR token_count >= 0",
            name="chunks_token_count_ck",
        ),
        CheckConstraint(
            "jsonb_typeof(heading_path) = 'array'",
            name="chunks_heading_path_array_ck",
        ),
        CheckConstraint(
            "content_hash IS NULL OR length(content_hash) = 64",
            name="chunks_content_hash_length_ck",
        ),
        CheckConstraint(
            "chunking_config_hash = '' OR length(chunking_config_hash) = 64",
            name="chunks_chunking_config_hash_length_ck",
        ),
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

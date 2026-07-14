"""Create the initial mikuRAG schema.

Revision ID: 0001
Revises:
"""

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_administrator", sa.Boolean(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("users_username_lower_uq", "users", [sa.text("lower(username)")], unique=True)

    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "knowledge_base_access",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "knowledge_base_id"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("safe_error", sa.Text(), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_base_id", "sha256", name="documents_kb_sha256_uq"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_documents_knowledge_base_id", "documents", ["knowledge_base_id"])

    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=768), nullable=True),
        sa.Column("embedding_model", sa.String(length=120), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "ordinal", name="chunks_document_ordinal_uq"),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    op.create_index("chunks_search_vector_gin", "chunks", ["search_vector"], postgresql_using="gin")
    op.create_index(
        "chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_owner_id", "conversations", ["owner_id"])
    op.create_index("ix_conversations_knowledge_base_id", "conversations", ["knowledge_base_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    op.create_table(
        "citations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("document_name", sa.String(length=255), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("retrieval_rank", sa.Integer(), nullable=False),
        sa.Column("retrieval_score", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_citations_document_id", "citations", ["document_id"])
    op.create_index("ix_citations_message_id", "citations", ["message_id"])

    op.create_table(
        "provider_configurations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("endpoint", sa.String(length=500), nullable=False),
        sa.Column("model_id", sa.String(length=160), nullable=False),
        sa.Column("encrypted_api_key", sa.LargeBinary(), nullable=True),
        sa.Column("is_validated", sa.Boolean(), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind"),
    )


def downgrade() -> None:
    op.drop_table("provider_configurations")
    op.drop_index("ix_citations_message_id", table_name="citations")
    op.drop_index("ix_citations_document_id", table_name="citations")
    op.drop_table("citations")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_knowledge_base_id", table_name="conversations")
    op.drop_index("ix_conversations_owner_id", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("chunks_embedding_hnsw", table_name="chunks", postgresql_using="hnsw")
    op.drop_index("chunks_search_vector_gin", table_name="chunks", postgresql_using="gin")
    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_table("chunks")
    op.drop_index("ix_documents_knowledge_base_id", table_name="documents")
    op.drop_table("documents")
    op.drop_table("knowledge_base_access")
    op.drop_table("knowledge_bases")
    op.drop_index("users_username_lower_uq", table_name="users")
    op.drop_table("users")


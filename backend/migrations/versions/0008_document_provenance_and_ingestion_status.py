"""Add source provenance and durable ingestion status.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("parser_version", sa.String(length=64)))
    op.add_column("documents", sa.Column("chunking_version", sa.String(length=64)))
    op.add_column(
        "documents",
        sa.Column("source_kind", sa.String(length=32), nullable=False, server_default="text"),
    )
    op.add_column("documents", sa.Column("language", sa.String(length=64)))
    op.add_column(
        "documents",
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("documents", sa.Column("source_uri", sa.String(length=2_048)))
    op.add_column("documents", sa.Column("source_path", sa.String(length=1_024)))
    op.add_column(
        "documents",
        sa.Column(
            "source_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "documents",
        sa.Column(
            "ingestion_stage", sa.String(length=32), nullable=False, server_default="queued"
        ),
    )
    op.add_column(
        "documents",
        sa.Column("ingestion_progress", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "documents",
        sa.Column("ingestion_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "documents",
        sa.Column(
            "ingestion_warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        """
        UPDATE documents
        SET source_kind = CASE media_type
            WHEN 'application/pdf' THEN 'pdf'
            WHEN 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                THEN 'docx'
            WHEN 'text/markdown' THEN 'markdown'
            WHEN 'text/html' THEN 'html'
            WHEN 'text/x-python' THEN 'code'
            WHEN 'text/javascript' THEN 'code'
            WHEN 'application/javascript' THEN 'code'
            WHEN 'text/jsx' THEN 'code'
            WHEN 'text/typescript' THEN 'code'
            WHEN 'text/tsx' THEN 'code'
            ELSE 'text'
        END,
        ingestion_stage = CASE status
            WHEN 'ready' THEN 'ready'
            WHEN 'failed' THEN 'failed'
            WHEN 'processing' THEN 'extract'
            WHEN 'deleting' THEN 'deleting'
            ELSE 'queued'
        END,
        ingestion_progress = CASE WHEN status = 'ready' THEN 100 ELSE 0 END
        """
    )
    op.create_check_constraint(
        "documents_source_kind_ck",
        "documents",
        "source_kind IN ('pdf', 'docx', 'text', 'markdown', 'html', 'code')",
    )
    op.create_check_constraint(
        "documents_ingestion_progress_ck",
        "documents",
        "ingestion_progress BETWEEN 0 AND 100",
    )
    op.create_check_constraint(
        "documents_ingestion_attempts_ck", "documents", "ingestion_attempts >= 0"
    )
    op.create_check_constraint(
        "documents_tags_array_ck", "documents", "jsonb_typeof(tags) = 'array'"
    )
    op.create_check_constraint(
        "documents_source_metadata_object_ck",
        "documents",
        "jsonb_typeof(source_metadata) = 'object'",
    )
    op.create_check_constraint(
        "documents_ingestion_warnings_array_ck",
        "documents",
        "jsonb_typeof(ingestion_warnings) = 'array'",
    )
    op.create_index(
        "documents_kb_source_kind_idx", "documents", ["knowledge_base_id", "source_kind"]
    )

    op.add_column(
        "upload_sessions",
        sa.Column("source_kind", sa.String(length=32), nullable=False, server_default="text"),
    )
    op.add_column("upload_sessions", sa.Column("language", sa.String(length=64)))
    op.add_column(
        "upload_sessions",
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("upload_sessions", sa.Column("source_uri", sa.String(length=2_048)))
    op.add_column("upload_sessions", sa.Column("source_path", sa.String(length=1_024)))
    op.add_column(
        "upload_sessions",
        sa.Column(
            "source_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.execute(
        """
        UPDATE upload_sessions
        SET source_kind = CASE suffix
            WHEN '.pdf' THEN 'pdf'
            WHEN '.docx' THEN 'docx'
            WHEN '.md' THEN 'markdown'
            WHEN '.markdown' THEN 'markdown'
            WHEN '.html' THEN 'html'
            WHEN '.htm' THEN 'html'
            WHEN '.py' THEN 'code'
            WHEN '.js' THEN 'code'
            WHEN '.jsx' THEN 'code'
            WHEN '.mjs' THEN 'code'
            WHEN '.cjs' THEN 'code'
            WHEN '.ts' THEN 'code'
            WHEN '.tsx' THEN 'code'
            ELSE 'text'
        END
        """
    )
    op.create_check_constraint(
        "upload_sessions_source_kind_ck",
        "upload_sessions",
        "source_kind IN ('pdf', 'docx', 'text', 'markdown', 'html', 'code')",
    )
    op.create_check_constraint(
        "upload_sessions_tags_array_ck", "upload_sessions", "jsonb_typeof(tags) = 'array'"
    )
    op.create_check_constraint(
        "upload_sessions_source_metadata_object_ck",
        "upload_sessions",
        "jsonb_typeof(source_metadata) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "upload_sessions_source_metadata_object_ck", "upload_sessions", type_="check"
    )
    op.drop_constraint("upload_sessions_tags_array_ck", "upload_sessions", type_="check")
    op.drop_constraint("upload_sessions_source_kind_ck", "upload_sessions", type_="check")
    op.drop_column("upload_sessions", "source_metadata")
    op.drop_column("upload_sessions", "source_path")
    op.drop_column("upload_sessions", "source_uri")
    op.drop_column("upload_sessions", "tags")
    op.drop_column("upload_sessions", "language")
    op.drop_column("upload_sessions", "source_kind")

    op.drop_index("documents_kb_source_kind_idx", table_name="documents")
    op.drop_constraint("documents_ingestion_warnings_array_ck", "documents", type_="check")
    op.drop_constraint("documents_source_metadata_object_ck", "documents", type_="check")
    op.drop_constraint("documents_tags_array_ck", "documents", type_="check")
    op.drop_constraint("documents_ingestion_attempts_ck", "documents", type_="check")
    op.drop_constraint("documents_ingestion_progress_ck", "documents", type_="check")
    op.drop_constraint("documents_source_kind_ck", "documents", type_="check")
    op.drop_column("documents", "ingestion_warnings")
    op.drop_column("documents", "ingestion_attempts")
    op.drop_column("documents", "ingestion_progress")
    op.drop_column("documents", "ingestion_stage")
    op.drop_column("documents", "source_metadata")
    op.drop_column("documents", "source_path")
    op.drop_column("documents", "source_uri")
    op.drop_column("documents", "tags")
    op.drop_column("documents", "language")
    op.drop_column("documents", "source_kind")
    op.drop_column("documents", "chunking_version")
    op.drop_column("documents", "parser_version")

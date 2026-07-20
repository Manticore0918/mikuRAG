"""Add durable resumable Document upload sessions.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("initiated_by_id", sa.Uuid(), nullable=True),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("suffix", sa.String(length=16), nullable=False),
        sa.Column("declared_sha256", sa.String(length=64), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("received_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("part_size_bytes", sa.Integer(), nullable=False),
        sa.Column("temporary_storage_key", sa.String(length=255), nullable=False),
        sa.Column("final_storage_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("safe_error", sa.Text(), nullable=True),
        sa.Column("resulting_document_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "received_bytes >= 0 AND received_bytes <= total_bytes",
            name="upload_sessions_received_bytes_ck",
        ),
        sa.CheckConstraint("total_bytes > 0", name="upload_sessions_total_bytes_ck"),
        sa.CheckConstraint("part_size_bytes > 0", name="upload_sessions_part_size_bytes_ck"),
        sa.ForeignKeyConstraint(
            ["initiated_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["resulting_document_id"], ["documents.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("final_storage_key"),
        sa.UniqueConstraint("resulting_document_id"),
        sa.UniqueConstraint("temporary_storage_key"),
    )
    op.create_index(
        "ix_upload_sessions_knowledge_base_id",
        "upload_sessions",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_upload_sessions_initiated_by_id",
        "upload_sessions",
        ["initiated_by_id"],
    )
    op.create_index(
        "upload_sessions_kb_status_idx",
        "upload_sessions",
        ["knowledge_base_id", "status"],
    )
    op.create_index(
        "upload_sessions_expires_at_idx", "upload_sessions", ["expires_at"]
    )
    op.create_index(
        "upload_sessions_open_kb_sha256_uq",
        "upload_sessions",
        ["knowledge_base_id", "declared_sha256"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )

    op.create_table(
        "upload_part_receipts",
        sa.Column("upload_session_id", sa.Uuid(), nullable=False),
        sa.Column("offset_bytes", sa.BigInteger(), nullable=False),
        sa.Column("length_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("offset_bytes >= 0", name="upload_part_receipts_offset_ck"),
        sa.CheckConstraint("length_bytes > 0", name="upload_part_receipts_length_ck"),
        sa.ForeignKeyConstraint(
            ["upload_session_id"], ["upload_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("upload_session_id", "offset_bytes"),
    )


def downgrade() -> None:
    op.drop_table("upload_part_receipts")
    op.drop_index("upload_sessions_open_kb_sha256_uq", table_name="upload_sessions")
    op.drop_index("upload_sessions_expires_at_idx", table_name="upload_sessions")
    op.drop_index("upload_sessions_kb_status_idx", table_name="upload_sessions")
    op.drop_index("ix_upload_sessions_initiated_by_id", table_name="upload_sessions")
    op.drop_index("ix_upload_sessions_knowledge_base_id", table_name="upload_sessions")
    op.drop_table("upload_sessions")

"""Add persistent bounded chunk re-index jobs.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reindex_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_chunking_version", sa.String(length=64), nullable=False),
        sa.Column("selection_mode", sa.String(length=20), nullable=False),
        sa.Column(
            "canary_percentage",
            sa.Integer(),
            nullable=False,
            server_default="100",
        ),
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="10"),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "total_documents",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "completed_documents",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "failed_documents",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "target_chunking_version IN ('legacy', 'hierarchical_v1')",
            name="reindex_jobs_target_version_ck",
        ),
        sa.CheckConstraint(
            "selection_mode IN ('canary', 'all')",
            name="reindex_jobs_selection_ck",
        ),
        sa.CheckConstraint(
            "canary_percentage BETWEEN 1 AND 100",
            name="reindex_jobs_canary_percentage_ck",
        ),
        sa.CheckConstraint(
            "batch_size BETWEEN 1 AND 100",
            name="reindex_jobs_batch_size_ck",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'paused', 'completed', 'failed', "
            "'cancelled')",
            name="reindex_jobs_status_ck",
        ),
        sa.CheckConstraint(
            "total_documents >= 0 AND completed_documents >= 0 "
            "AND failed_documents >= 0 "
            "AND completed_documents + failed_documents <= total_documents",
            name="reindex_jobs_counts_ck",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reindex_jobs_created_by_id",
        "reindex_jobs",
        ["created_by_id"],
    )
    op.create_index(
        "ix_reindex_jobs_knowledge_base_id",
        "reindex_jobs",
        ["knowledge_base_id"],
    )
    op.create_index(
        "reindex_jobs_status_created_idx",
        "reindex_jobs",
        ["status", "created_at"],
    )

    op.create_table(
        "reindex_items",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("safe_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')",
            name="reindex_items_status_ck",
        ),
        sa.CheckConstraint("attempts >= 0", name="reindex_items_attempts_ck"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["reindex_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", "document_id"),
    )
    op.create_index(
        "reindex_items_job_status_idx",
        "reindex_items",
        ["job_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("reindex_items_job_status_idx", table_name="reindex_items")
    op.drop_table("reindex_items")
    op.drop_index("reindex_jobs_status_created_idx", table_name="reindex_jobs")
    op.drop_index("ix_reindex_jobs_knowledge_base_id", table_name="reindex_jobs")
    op.drop_index("ix_reindex_jobs_created_by_id", table_name="reindex_jobs")
    op.drop_table("reindex_jobs")

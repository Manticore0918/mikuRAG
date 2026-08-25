"""Add hierarchical chunk metadata and parent storage.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("parent_chunk_id", sa.Uuid(), nullable=True))
    op.add_column("chunks", sa.Column("chunk_level", sa.String(length=32), nullable=True))
    op.add_column("chunks", sa.Column("start_page", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("end_page", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("start_offset", sa.BigInteger(), nullable=True))
    op.add_column("chunks", sa.Column("end_offset", sa.BigInteger(), nullable=True))
    op.add_column(
        "chunks",
        sa.Column(
            "heading_path",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column("chunks", sa.Column("content_type", sa.String(length=32), nullable=True))
    op.add_column("chunks", sa.Column("token_count", sa.Integer(), nullable=True))
    op.add_column("chunks", sa.Column("chunking_version", sa.String(length=64), nullable=True))
    op.add_column("chunks", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.alter_column(
        "chunks",
        "embedding_model",
        existing_type=sa.String(length=120),
        nullable=True,
    )
    op.create_foreign_key(
        "chunks_parent_chunk_fk",
        "chunks",
        "chunks",
        ["parent_chunk_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.execute(
        """
        UPDATE chunks
        SET chunk_level = 'child',
            start_page = CASE
                WHEN jsonb_typeof(locator -> 'start_page') = 'number'
                    THEN (locator ->> 'start_page')::integer
                WHEN jsonb_typeof(locator -> 'page') = 'number'
                    THEN (locator ->> 'page')::integer
                ELSE NULL
            END,
            end_page = CASE
                WHEN jsonb_typeof(locator -> 'end_page') = 'number'
                    THEN (locator ->> 'end_page')::integer
                WHEN jsonb_typeof(locator -> 'page') = 'number'
                    THEN (locator ->> 'page')::integer
                ELSE NULL
            END,
            heading_path = CASE
                WHEN jsonb_typeof(locator -> 'heading_path') = 'array'
                    THEN locator -> 'heading_path'
                ELSE '[]'::jsonb
            END,
            content_type = 'mixed',
            chunking_version = 'legacy'
        """
    )

    op.alter_column(
        "chunks",
        "chunk_level",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default="child",
    )
    op.alter_column(
        "chunks",
        "heading_path",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )
    op.alter_column(
        "chunks",
        "content_type",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default="mixed",
    )
    op.alter_column(
        "chunks",
        "chunking_version",
        existing_type=sa.String(length=64),
        nullable=False,
        server_default="legacy",
    )

    op.drop_constraint("chunks_document_ordinal_uq", "chunks", type_="unique")
    op.create_unique_constraint(
        "chunks_document_level_ordinal_uq",
        "chunks",
        ["document_id", "chunk_level", "ordinal"],
    )
    op.create_index(
        "ix_chunks_parent_chunk_id",
        "chunks",
        ["parent_chunk_id"],
    )
    op.create_check_constraint(
        "chunks_level_ck",
        "chunks",
        "chunk_level IN ('child', 'parent', 'section_summary', 'document_summary')",
    )
    op.create_check_constraint(
        "chunks_content_type_ck",
        "chunks",
        "content_type IN ('paragraph', 'list', 'table', 'code', 'mixed', 'summary')",
    )
    op.create_check_constraint(
        "chunks_parent_not_self_ck",
        "chunks",
        "parent_chunk_id IS NULL OR parent_chunk_id <> id",
    )
    op.create_check_constraint(
        "chunks_start_page_ck",
        "chunks",
        "start_page IS NULL OR start_page > 0",
    )
    op.create_check_constraint(
        "chunks_end_page_ck",
        "chunks",
        "end_page IS NULL OR end_page > 0",
    )
    op.create_check_constraint(
        "chunks_page_range_ck",
        "chunks",
        "start_page IS NULL OR end_page IS NULL OR end_page >= start_page",
    )
    op.create_check_constraint(
        "chunks_start_offset_ck",
        "chunks",
        "start_offset IS NULL OR start_offset >= 0",
    )
    op.create_check_constraint(
        "chunks_end_offset_ck",
        "chunks",
        "end_offset IS NULL OR end_offset >= 0",
    )
    op.create_check_constraint(
        "chunks_offset_range_ck",
        "chunks",
        "start_offset IS NULL OR end_offset IS NULL OR end_offset >= start_offset",
    )
    op.create_check_constraint(
        "chunks_token_count_ck",
        "chunks",
        "token_count IS NULL OR token_count >= 0",
    )
    op.create_check_constraint(
        "chunks_heading_path_array_ck",
        "chunks",
        "jsonb_typeof(heading_path) = 'array'",
    )
    op.create_check_constraint(
        "chunks_content_hash_length_ck",
        "chunks",
        "content_hash IS NULL OR length(content_hash) = 64",
    )


def downgrade() -> None:
    op.execute("UPDATE chunks SET parent_chunk_id = NULL")
    op.execute("DELETE FROM chunks WHERE chunk_level <> 'child'")
    op.execute("UPDATE chunks SET embedding_model = 'unknown' WHERE embedding_model IS NULL")

    op.drop_constraint("chunks_content_hash_length_ck", "chunks", type_="check")
    op.drop_constraint("chunks_heading_path_array_ck", "chunks", type_="check")
    op.drop_constraint("chunks_token_count_ck", "chunks", type_="check")
    op.drop_constraint("chunks_offset_range_ck", "chunks", type_="check")
    op.drop_constraint("chunks_end_offset_ck", "chunks", type_="check")
    op.drop_constraint("chunks_start_offset_ck", "chunks", type_="check")
    op.drop_constraint("chunks_page_range_ck", "chunks", type_="check")
    op.drop_constraint("chunks_end_page_ck", "chunks", type_="check")
    op.drop_constraint("chunks_start_page_ck", "chunks", type_="check")
    op.drop_constraint("chunks_parent_not_self_ck", "chunks", type_="check")
    op.drop_constraint("chunks_content_type_ck", "chunks", type_="check")
    op.drop_constraint("chunks_level_ck", "chunks", type_="check")
    op.drop_index("ix_chunks_parent_chunk_id", table_name="chunks")
    op.drop_constraint("chunks_document_level_ordinal_uq", "chunks", type_="unique")
    op.create_unique_constraint(
        "chunks_document_ordinal_uq",
        "chunks",
        ["document_id", "ordinal"],
    )
    op.drop_constraint("chunks_parent_chunk_fk", "chunks", type_="foreignkey")

    op.alter_column(
        "chunks",
        "embedding_model",
        existing_type=sa.String(length=120),
        nullable=False,
    )
    op.drop_column("chunks", "content_hash")
    op.drop_column("chunks", "chunking_version")
    op.drop_column("chunks", "token_count")
    op.drop_column("chunks", "content_type")
    op.drop_column("chunks", "heading_path")
    op.drop_column("chunks", "end_offset")
    op.drop_column("chunks", "start_offset")
    op.drop_column("chunks", "end_page")
    op.drop_column("chunks", "start_page")
    op.drop_column("chunks", "chunk_level")
    op.drop_column("chunks", "parent_chunk_id")

"""Persist the canonical chunker configuration hash on every chunk.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chunks",
        sa.Column(
            "chunking_config_hash",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    )
    op.create_check_constraint(
        "chunks_chunking_config_hash_length_ck",
        "chunks",
        "chunking_config_hash = '' OR length(chunking_config_hash) = 64",
    )


def downgrade() -> None:
    op.drop_constraint(
        "chunks_chunking_config_hash_length_ck", "chunks", type_="check"
    )
    op.drop_column("chunks", "chunking_config_hash")

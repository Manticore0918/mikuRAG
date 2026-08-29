"""Add authoritative Knowledge Base index generations for cache invalidation.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_bases",
        sa.Column(
            "index_generation",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "knowledge_bases_index_generation_ck",
        "knowledge_bases",
        "index_generation >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "knowledge_bases_index_generation_ck",
        "knowledge_bases",
        type_="check",
    )
    op.drop_column("knowledge_bases", "index_generation")

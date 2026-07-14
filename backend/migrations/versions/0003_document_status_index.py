"""Index Document lifecycle queries.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "documents_kb_status_idx",
        "documents",
        ["knowledge_base_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("documents_kb_status_idx", table_name="documents")

"""Add deterministic Message ordering and one active turn per Conversation.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("sequence", sa.Integer(), nullable=True))
    op.execute(
        """
        WITH ordered AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY conversation_id ORDER BY created_at, id
                   ) AS sequence
            FROM messages
        )
        UPDATE messages
        SET sequence = ordered.sequence
        FROM ordered
        WHERE messages.id = ordered.id
        """
    )
    op.alter_column("messages", "sequence", nullable=False)
    op.create_unique_constraint(
        "messages_conversation_sequence_uq",
        "messages",
        ["conversation_id", "sequence"],
    )
    op.create_index(
        "messages_one_streaming_assistant_uq",
        "messages",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("role = 'assistant' AND status = 'streaming'"),
    )


def downgrade() -> None:
    op.drop_index("messages_one_streaming_assistant_uq", table_name="messages")
    op.drop_constraint(
        "messages_conversation_sequence_uq",
        "messages",
        type_="unique",
    )
    op.drop_column("messages", "sequence")

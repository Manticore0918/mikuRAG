"""Create a true BM25 index over chunk text via pg_search.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# Forward-safe by design (ADR-0005): the pg_search extension ships in the
# ParadeDB image but not in the stock pgvector image. Both steps are guarded so
# a database that never had pg_search migrates cleanly to the same revision
# head, leaving the BM25 index absent and retrieval on the FTS fallback path.
_CREATE_EXTENSION_GUARDED = """
DO $migration$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_search') THEN
        CREATE EXTENSION IF NOT EXISTS pg_search;
    END IF;
END
$migration$;
"""

_CREATE_INDEX_GUARDED = """
DO $migration$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_search') THEN
        BEGIN
            CREATE INDEX chunks_search_bm25 ON chunks
                USING bm25 (id, text)
                WITH (key_field = 'id');
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END;
    END IF;
END
$migration$;
"""


def upgrade() -> None:
    op.execute(_CREATE_EXTENSION_GUARDED)
    op.execute(_CREATE_INDEX_GUARDED)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS chunks_search_bm25")
    op.execute("DROP EXTENSION IF EXISTS pg_search")

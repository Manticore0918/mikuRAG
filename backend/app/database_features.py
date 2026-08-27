"""Idempotent reconciliation for optional database capabilities.

Alembic revisions are immutable checkpoints. Optional extensions are different:
an installation may reach the current revision on the stock pgvector image and
switch to ParadeDB later. Reconciliation is therefore run after every normal
migration command so newly available capabilities can be installed without
rewinding schema history or re-uploading Documents.
"""

import logging
from typing import Literal, TypedDict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import engine

logger = logging.getLogger(__name__)


class Bm25FeatureStatus(TypedDict):
    status: Literal["ready", "unavailable", "error"]
    detail: str


_CREATE_BM25_INDEX = """
DO $reconcile$
BEGIN
    IF to_regclass('chunks_search_bm25') IS NULL THEN
        CREATE INDEX chunks_search_bm25 ON chunks
            USING bm25 (id, text)
            WITH (key_field = 'id');
    END IF;
END
$reconcile$;
"""


async def reconcile_optional_database_features(
    database_engine: AsyncEngine = engine,
) -> Bm25FeatureStatus:
    """Install pg_search and its index when the running image provides them.

    Failure is non-fatal because PostgreSQL FTS is the stable compatibility
    path. The caller prints the returned status, and readiness independently
    reports whether BM25 is active.
    """
    try:
        async with database_engine.begin() as connection:
            available = bool(
                (
                    await connection.execute(
                        text(
                            "SELECT EXISTS ("
                            "SELECT 1 FROM pg_available_extensions "
                            "WHERE name = 'pg_search'"
                            ")"
                        )
                    )
                ).scalar()
            )
            if not available:
                return {
                    "status": "unavailable",
                    "detail": "pg_search is not provided by the configured database image",
                }

            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('mikurag:pg-search'))")
            )
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_search"))
            version_state = str(
                (
                    await connection.execute(
                        text(
                            "SELECT installed_version || '|' || default_version "
                            "FROM pg_available_extensions WHERE name = 'pg_search'"
                        )
                    )
                ).scalar()
                or ""
            )
            installed_version, _, default_version = version_state.partition("|")
            extension_upgraded = bool(
                installed_version
                and default_version
                and installed_version != default_version
            )
            if extension_upgraded:
                await connection.execute(text("ALTER EXTENSION pg_search UPDATE"))
            await connection.execute(text(_CREATE_BM25_INDEX))
            if extension_upgraded:
                # pg_search indexes persist extension-owned scan state. Rebuild
                # after an extension upgrade so an index created by the prior
                # version cannot retain invalid CTIDs or serialized plan state.
                await connection.execute(text("REINDEX INDEX chunks_search_bm25"))
        return {
            "status": "ready",
            "detail": (
                "pg_search extension and chunks_search_bm25 index are ready"
                + (f" at {default_version}" if default_version else "")
            ),
        }
    except Exception as error:  # pragma: no cover - depends on database privileges/dialect
        reason = str(getattr(error, "orig", error)).splitlines()[0][:300]
        logger.warning(
            "Optional BM25 reconciliation failed (%s: %s); FTS remains active",
            type(error).__name__,
            error,
        )
        return {
            "status": "error",
            "detail": (
                f"BM25 reconciliation failed: {reason}; "
                "PostgreSQL FTS remains active"
            ),
        }

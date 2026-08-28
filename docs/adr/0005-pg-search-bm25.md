# Add a true BM25 lexical leg via the pg_search extension

Set `POSTGRES_IMAGE=pgvector/pgvector:pg16` to select the stock FTS fallback
without editing Compose or re-uploading Documents. Set
`POSTGRES_SHARED_PRELOAD_LIBRARIES=` at the same time because the stock image
does not ship the `pg_search` shared library.

Compatibility was executed on 2026-08-28 against image digest
`sha256:64ebec73c00a367d217022caacb7762745c06b3f84dbbc1247b1480c854819d2`.
PostgreSQL reported server version `160015`; both `pg_search` and `vector` were
available; extension creation, the `bm25` index, bound-text `@@@`, typed
`pdb.match(...)` queries containing apostrophes, and `pdb.score(id)` all
completed successfully. The Bash and PowerShell spike scripts run this same
check in an isolated, uniquely named container.

The normal `migrate` service runs Alembic and then idempotently reconciles
image-dependent features. This matters when an existing installation reached
revision `0010` on stock pgvector and later switches to ParadeDB: schema history
does not rewind, but the newly available extension and BM25 index are created.
When the image provides a newer extension patch, reconciliation upgrades the
extension and rebuilds the BM25 index in the same advisory-lock-protected
transaction. Reconciliation remains non-fatal; a privilege or dialect failure
leaves PostgreSQL FTS active and is surfaced by readiness.

The operational churn spike found that both pg_search 0.24.3 and 0.25.5 can
retain stale CTIDs after cascaded hard deletes from the shared `chunks` table.
The failure recurred deterministically after evaluation-KB cleanup; `VACUUM`
and planner toggles did not repair it, while `REINDEX INDEX
chunks_search_bm25` did. mikuRAG therefore performs the advisory-lock-protected
reindex at its two hard-delete boundaries: background Document purge and
isolated evaluation cleanup. Inserts and soft lifecycle transitions do not run
this maintenance. Stock PostgreSQL deployments skip it because the extension
and index are absent.

Compose explicitly starts PostgreSQL with `shared_preload_libraries=pg_search`.
This is required for existing pgvector volumes because ParadeDB's first-run
initialization does not rewrite a pre-existing `postgresql.conf`.

When the old and new images ship different libc collation versions, PostgreSQL
warns until collation-dependent indexes are rebuilt. Pause API/worker traffic,
run `REINDEX DATABASE mikurag`, then run
`ALTER DATABASE mikurag REFRESH COLLATION VERSION` from another database before
resuming traffic. This lock-taking maintenance is intentionally not automatic.

mikuRAG adds a true BM25 lexical leg powered by the `pg_search` (ParadeDB)
extension, pinned to the PostgreSQL 16 image
`paradedb/paradedb:0.25.5-pg16`, while retaining PostgreSQL FTS as the
always-available fallback. Migration 0010 creates the extension and
`chunks_search_bm25` index (`CREATE INDEX ... USING bm25 (id, text) WITH
(key_field = 'id')`) behind availability guards, so the stock
`pgvector/pgvector:pg16` image still migrates to the same revision head. Version
0.25.5 queries use the typed dialect `WHERE text @@@ pdb.match(<bound text>)
ORDER BY pdb.score(id) DESC`; this treats user punctuation as tokenizer input
instead of raw query-parser syntax. The BM25 statement runs inside a savepoint
so any extension/dialect failure can roll back locally and retry through
`websearch_to_tsquery` + `ts_rank_cd` on the same Session. Authorization and
user-selectable filters remain in the SQL before candidate limits.
`is_bm25_available` checks the extension and index on every pipeline build, and
readiness reports BM25 without gating core readiness. Production keeps BM25
feature-off until the evaluation gate passes; `bm25_hybrid_enabled` defaults to
`false`. The spike scripts are the executable compatibility proof for the
pinned image, PostgreSQL major, extensions, index syntax, typed `@@@` query, and
`pdb.score` function.

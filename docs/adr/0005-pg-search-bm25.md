# Add a true BM25 lexical leg via the pg_search extension

Set `POSTGRES_IMAGE=pgvector/pgvector:pg16` to select the stock FTS fallback
without editing Compose or re-uploading Documents.

Compatibility was executed on 2026-08-28 against image digest
`sha256:21518b4005ad4111bdd41a6687ef37ca2abc363d7af30e700900ad6b8cb33e1e`.
PostgreSQL reported server version `160014`; both `pg_search` and `vector` were
available; extension creation, the `bm25` index, bound-text `@@@`, and
`pdb.score(id)` all completed successfully. The Bash and PowerShell spike
scripts run this same check in an isolated, uniquely named container.

mikuRAG adds a true BM25 lexical leg powered by the `pg_search` (ParadeDB) extension, pinned to the PostgreSQL 16 image `paradedb/paradedb:0.24.1-pg16`, while retaining PostgreSQL FTS as the always-available fallback. Migration 0010 creates the extension and `chunks_search_bm25` index (`CREATE INDEX ... USING bm25 (id, text) WITH (key_field = 'id')`) behind availability guards, so the stock `pgvector/pgvector:pg16` image still migrates to the same revision head. Version 0.24.1 queries use the pinned dialect `WHERE text @@@ <bound text> ORDER BY pdb.score(id) DESC`; the BM25 statement runs inside a savepoint so any extension/dialect failure can roll back locally and retry through `websearch_to_tsquery` + `ts_rank_cd` on the same Session. Authorization and user-selectable filters remain in the SQL before candidate limits. `is_bm25_available` checks the extension and index on every pipeline build, and readiness reports BM25 without gating core readiness. Production keeps BM25 feature-off until the evaluation gate passes; `bm25_hybrid_enabled` defaults to `false`. `scripts/spike_pg_search.sh` is the executable compatibility proof for the pinned image, PostgreSQL major, extensions, index syntax, bound-text `@@@` query, and `pdb.score` function.

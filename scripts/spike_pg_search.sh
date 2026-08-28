#!/usr/bin/env bash
#
# spike_pg_search.sh — confirm the pg_search (ParadeDB) BM25 dialect on the
# pinned database image before the checkpoint-3 evaluation gate.
#
# The development shell cannot reach Docker Hub, so this script is meant to run
# once in a networked environment to confirm two things the ADR-0005 decision
# depends on:
#   1. the pinned `paradedb/paradedb` image tag pulls and serves PostgreSQL 16
#      with the `pg_search` and `vector` extensions available; and
#   2. the BM25 dialect we query with — `@@@` with bound text and
#      `pdb.score(id)` — works over the `chunks_search_bm25`-style index.
#
# Usage:
#   ./scripts/spike_pg_search.sh [IMAGE_TAG]
#
# IMAGE_TAG defaults to the tag pinned in compose.yaml. The script is a no-op
# with a clear failure message on the exact tag/extension that did not match,
# so fixing is a one-line change in compose.yaml + ADR-0005.
#
set -euo pipefail

IMAGE_TAG="${1:-paradedb/paradedb:0.25.5-pg16}"
CONTAINER="mikurag-pg-search-spike-$$"
PG_USER="mikurag"
PG_PASSWORD="spike"
PG_DB="mikurag"

cleanup() {
  docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> Pulling and starting ${IMAGE_TAG} (this needs Docker Hub access)"
if ! docker run -d --name "${CONTAINER}" \
  -e POSTGRES_USER="${PG_USER}" \
  -e POSTGRES_PASSWORD="${PG_PASSWORD}" \
  -e POSTGRES_DB="${PG_DB}" \
  "${IMAGE_TAG}" >/dev/null 2>&1; then
  echo "ERROR: could not start image '${IMAGE_TAG}'."
  echo "       Confirm the tag exists, e.g.: docker manifest inspect ${IMAGE_TAG}"
  echo "       then update compose.yaml (postgres.image) and ADR-0005."
  exit 1
fi

echo "==> Waiting for PostgreSQL initialization to finish"
for i in $(seq 1 60); do
  if docker exec "${CONTAINER}" psql -U "${PG_USER}" -d postgres -Atqc \
    "SELECT 1 FROM pg_database WHERE datname = '${PG_DB}'" 2>/dev/null | grep -qx 1; then
    break
  fi
  if [ "${i}" -eq 60 ]; then
    echo "ERROR: PostgreSQL did not finish initializing inside the spike container."
    exit 1
  fi
  sleep 1
done

echo "==> Confirming extensions and the BM25 dialect"
DIALECT_SQL=$(cat <<'SQL'
SELECT current_setting('server_version_num') AS pg_version,
       EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_search') AS pg_search_available,
       EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') AS vector_available;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_search;
CREATE TABLE spike_docs (id bigserial PRIMARY KEY, text text NOT NULL);
CREATE INDEX spike_docs_bm25 ON spike_docs USING bm25 (id, text) WITH (key_field = 'id');
INSERT INTO spike_docs (text) VALUES
  ('The quick brown fox jumps over the lazy dog.'),
  ('PostgreSQL full text search ranks documents by ts_rank.'),
  ('ParadeDB provides BM25 ranking over PostgreSQL tables.');
SELECT id,
       pdb.score(id) AS bm25_score,
       left(text, 40) AS snippet
FROM spike_docs
WHERE text @@@ 'bm25 search ranking'
ORDER BY pdb.score(id) DESC
LIMIT 3;
SELECT id,
       pdb.score(id) AS bm25_score
FROM spike_docs
WHERE text @@@ pdb.match('What does PostgreSQL''s BM25 ranking provide?')
ORDER BY pdb.score(id) DESC
LIMIT 3;
SQL
)

if ! docker exec -i "${CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" \
  -v ON_ERROR_STOP=1 -f - <<<"${DIALECT_SQL}"; then
  echo "ERROR: the BM25 dialect check failed against ${IMAGE_TAG}."
  echo "       Adjust the retriever (app/rag/retrievers.py) or the image tag,"
  echo "       then re-run this spike before the evaluation gate."
  exit 1
fi

echo ""
echo "==> SPIKE PASSED on ${IMAGE_TAG}"
echo "    pg_search + vector extensions available; @@@ bound text /"
echo "    pdb.score(id) dialect confirmed over a bm25 index."

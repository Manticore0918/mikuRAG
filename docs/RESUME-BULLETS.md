# Truthful portfolio and resume bullets

These bullets deliberately separate implemented scope from measured diagnostics.
Update them only from committed release evidence.

- Built a self-hosted, multi-user FastAPI RAG system with durable asynchronous
  ingestion for PDF, DOCX, text, Markdown, HTML, Python, JavaScript, and
  TypeScript; PostgreSQL/pgvector retrieval; validated grounded answers; and
  server-owned source Citations.
- Created a versioned RAG evaluation harness over a redistributable 64-question,
  14-document synthetic corpus, reporting Recall@K, MRR, NDCG, Citation and
  claim-support metrics, latency percentiles, tokens, cache outcomes, and
  versioned cost estimates. The corpus scope and headline restriction are
  recorded in [`evidence/retrieval-ablation-gold-v1-test-2026-08-28.json`](./evidence/retrieval-ablation-gold-v1-test-2026-08-28.json).
- Implemented vector, PostgreSQL FTS, pg_search BM25, RRF, metadata filtering,
  query rewriting, and optional local cross-encoder retrieval paths with
  observable fused-order fallback and authorization predicates applied before
  candidate limits.
- Added fail-open Redis derived caches whose HMAC-scoped keys include an
  authoritative PostgreSQL index generation, preventing stale result reuse after
  Document publication, deletion, or re-indexing without caching final answers.
- Automated backend/frontend tests, migration upgrades, real PostgreSQL/Redis
  integration checks, container builds, Compose smoke evaluation, immutable GHCR
  release images, SBOMs, and rollback notes; added opt-in OpenTelemetry traces
  and metrics with a tested no-content telemetry boundary.

## Diagnostic result that must stay qualified

On the frozen 13-case `gold_v1` test slice, hybrid RRF reached 0.9615 Recall@10
at 50.6 ms retrieval p95, while the local cross-encoder reached 0.9107 NDCG@10
but 4,914.3 ms p95. This supports the engineering decision to leave the learned
reranker off by default; it is not a general quality claim because the corpus is
small, synthetic, and marked `headline_eligible: false`.


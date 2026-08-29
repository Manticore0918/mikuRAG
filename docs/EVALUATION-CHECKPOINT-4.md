# Checkpoint 4 — faithfulness, accounting, and caching

Checkpoint 4 is in progress. The first implementation slice establishes the
contracts needed to measure answer support and cache behavior without changing
the stable retrieval defaults.

## Implemented foundation

- `gold_v1` uses evaluation schema v3. Every case records expected claims,
  acceptable answer facts, required evidence, refusal expectation, and whether
  the evidence is intentionally conflicting. The manifest remains generated and
  verified from `build_manifest.py`.
- The deterministic `deterministic_claim_support` evaluator is versioned as
  `1.0.0`. It reports passage-level Citation precision/recall, claim Citation
  coverage, unsupported-Citation rate, refusal correctness, answer completeness,
  and claim support. Reports preserve retrieval metrics separately and include
  faithfulness by question category.
- Each production and evaluation turn records redacted stage durations for
  rewrite, query embedding, vector, lexical/BM25, fusion, reranking, expansion,
  generation, validation, persistence, retrieval, and total latency. Aggregate
  reports include p50/p95/p99, token volumes, cache outcomes, and ingestion
  throughput.
- API-cost estimates use the checked-in `pricing_v1.json` ledger. Local
  generation is recorded as zero API spend and explicitly excludes compute
  cost. Tokens without a reviewed price remain visible as unpriced; they are not
  silently assigned a zero cost.
- Query-embedding and retrieval-result Redis caches are optional and disabled by
  default. Keys use an HMAC of normalized query text and include Knowledge Base,
  normalized filters, embedding model, chunking version, retrieval configuration
  hash, and the authoritative Knowledge Base index generation. Values never
  contain query text or final answers.
- Retrieval cache hits reload chunk IDs through PostgreSQL with Ready-state,
  Knowledge Base, and user-filter predicates. Redis read/write errors and invalid
  or oversized entries fall back to the uncached path.
- Migration `0011` adds the authoritative generation. It is
  incremented when a Document becomes Ready, enters deletion, is purged, or is
  re-indexed through the normal ingestion path.

## Enabling a cold/warm experiment

Apply migrations, then opt in explicitly:

```dotenv
MIKURAG_QUERY_EMBEDDING_CACHE_ENABLED=true
MIKURAG_RETRIEVAL_CACHE_ENABLED=true
MIKURAG_RAG_CACHE_TTL_SECONDS=900
MIKURAG_RAG_CACHE_MAX_ENTRY_BYTES=262144
```

Run the same frozen evaluation configuration once cold and once warm. Compare
`turn_measurements.cache`, the per-stage latency percentiles, and the pricing
version in the generated report. Do not compare runs with different dataset,
git, chunking, embedding, or retrieval identities.

## Remaining before the exit gate

- Human-review the generated claim-to-evidence mappings and add a calibrated
  audit subset. Decide whether a separately versioned LLM judge adds value; the
  deterministic evaluator remains the baseline either way.
- Add reviewed external embedding/generation prices or an operator-supplied
  ledger before publishing a complete API-spend figure.
- Add PostgreSQL/Redis integration tests that demonstrate cold/warm equivalence,
  immediate deletion/re-index invalidation, cross-Knowledge-Base isolation, and
  a complete Grounded Answer while Redis is unavailable.
- Commit a real cold/warm quality/latency/cost report and a representative
  trace-style waterfall. No placeholder performance numbers are permitted.

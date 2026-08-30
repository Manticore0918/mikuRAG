# ADR 0009: Reranker failure falls back to the fused retrieval order

- Status: Accepted (2026-08-30)
- Context: Checkpoint 3 retrieval reliability

## Context

The optional local cross-encoder can improve ranking, but it adds a large model,
CPU/memory pressure, startup time, and an inference timeout to the question path.
Retrieval must remain available when the optional dependency, model files, or
inference capacity are unavailable.

## Decision

The stable default is hybrid vector plus PostgreSQL FTS with reciprocal-rank
fusion and no learned reranker. The cross-encoder is lazy-loaded only when the
explicit reranked mode is selected. Its inference is bounded by a wall-clock
timeout and a concurrency semaphore.

If model loading, queueing, inference, score conversion, or the timeout fails,
the request uses the already-computed RRF fused order. The response is not
reported as successfully reranked: turn measurements set the effective provider
to `fallback_fused_order`, and evaluation artifacts reject silent provider
fallbacks when a configuration is meant to measure the cross-encoder.

## Consequences

- An optional ranking component cannot make Grounded Chat unavailable.
- Fallback preserves the authorization, Ready-state, metadata-filter, evidence
  budget, grounding, and Citation checks already applied around retrieval.
- A failed cross-encoder can reduce ranking quality relative to the requested
  experiment, so the fallback is observable and cannot support a reranker claim.
- The measured 2026-08-28 diagnostic run exceeded the 1,500 ms p95 retrieval
  target; the learned reranker therefore remains disabled by default.


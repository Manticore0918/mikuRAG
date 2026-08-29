# ADR 0006: PostgreSQL generations invalidate Redis derived caches

## Status

Accepted for the Checkpoint 4 experimental path. Both caches remain disabled by
default until the evaluation gate is complete.

## Context

Redis is useful for query embeddings and ranked result IDs, but it cannot become
an authority for source availability or authorization. Deleting or re-indexing a
Document must prevent stale evidence immediately, including when key deletion in
Redis fails. Cache keys also cannot expose private query text.

## Decision

PostgreSQL stores a monotonic `knowledge_bases.index_generation`. The generation
is incremented in the same database transaction that publishes Ready chunks and
when deletion begins; purge also increments it defensively. Cache identities
include that generation plus the Knowledge Base ID, HMAC query digest, normalized
filters, embedding model, chunking version, and retrieval-configuration hash.

Redis values are bounded, TTL-scoped derived data. Query-embedding entries contain
only numeric vectors. Retrieval entries contain only chunk IDs, scores, and the
sufficiency decision. On a hit, PostgreSQL reloads those IDs under the current
Knowledge Base, Ready-state, and metadata-filter scope before evidence text is
assembled. A missing row, invalid entry, Redis error, or entry-size violation is
a cache miss and runs the stable uncached path.

## Consequences

- Invalidation does not require scanning or deleting Redis keys.
- A Redis reset loses performance only; PostgreSQL remains authoritative.
- Every Knowledge Base mutation intentionally makes its prior cache entries
  unreachable until TTL expiry.
- Generation reads add a small PostgreSQL lookup to cache-enabled turns.
- Hierarchical retrieval-result caching remains disabled because merged evidence
  can span multiple source chunks; query-embedding caching is still safe there.

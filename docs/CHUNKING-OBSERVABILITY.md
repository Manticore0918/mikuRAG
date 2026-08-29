# Hierarchical chunking observations

mikuRAG emits one-line JSON observations through the existing Python logging pipeline.
Every record begins with `mikurag_observation ` so log collectors can parse and route it
without requiring another metrics service.

The events are:

- `document_ingestion`: document and chunking-version identifiers, outcome and terminal
  stage, extracted and warning counts, parent/child/summary counts, child token
  distribution, cross-page percentage, ingestion duration, embedding duration, embedding
  request count, and embedding input count.
- `retrieval_decision`: retrieval mode and sufficiency, semantic/lexical/fused/reranked
  candidate counts, selected evidence and document counts, selected chunking versions,
  candidate/reranking/total duration, neighbor and parent expansion counts, evidence token
  count, and drop reasons.
- `summary_retrieval_decision`: summary candidate and selection counts, selected summary
  levels, context tokens, timing, and drop reasons.
- `rag_turn_failure`: terminal turn stage and a bounded failure category. Retrieval failure
  rate can be calculated from failures whose terminal stage is `retrieving`.

Request- and task-scoped records additionally carry a `correlation_id` field: the
`X-Request-ID` assigned by the API middleware and propagated to Celery tasks
(`docs/OBSERVABILITY.md`). Background events without an active request or task context
keep the historical schema without that field.

Insufficient-evidence rate is calculated from `retrieval_decision` records where
`sufficient` is false. Neighbor expansion frequency is the proportion of retrieval records
where `neighbor_expansion_count` or `parent_promotion_count` is non-zero.

Observation records intentionally exclude queries, document names, source text, excerpts,
headings, and generated answers. UUIDs, model-independent version names, counts, durations,
and bounded outcome labels are the only identifying or descriptive fields.

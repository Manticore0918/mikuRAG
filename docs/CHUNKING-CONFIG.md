# Hierarchical chunking configuration

The hierarchical architecture is opt-in. The default configuration continues to use
legacy character chunking and legacy retrieval until an environment is deliberately
migrated.

## Rollout

1. Apply the database migration before changing feature flags.
2. Set `MIKURAG_CHUNKING_VERSION=hierarchical_v1` on the API and worker services.
3. Re-ingest Documents that should receive parent/child chunks. Changing the setting
   does not rewrite existing chunks automatically.
4. Enable `MIKURAG_HIERARCHICAL_RETRIEVAL_ENABLED=true` after representative Documents
   have been re-ingested and retrieval has been evaluated.
5. Optionally enable `MIKURAG_SUMMARY_GENERATION_ENABLED=true`. Summary generation
   requires hierarchical chunking and is best-effort, so a summary-provider failure
   does not discard valid child chunks.

The token controls are:

- `MIKURAG_CHUNK_TOKENIZER`
- `MIKURAG_CHILD_MIN_TOKENS`
- `MIKURAG_CHILD_TARGET_TOKENS`
- `MIKURAG_CHILD_MAX_TOKENS`
- `MIKURAG_CHILD_OVERLAP_TOKENS`
- `MIKURAG_PARENT_TARGET_TOKENS`
- `MIKURAG_PARENT_MAX_TOKENS`

Retrieval is controlled by the semantic and lexical candidate counts, rerank count,
evidence item and token budgets, maximum merged-passage size, neighbor expansion
count, and document-diversity penalty in `.env.example`.

## Rollback

Set `MIKURAG_HIERARCHICAL_RETRIEVAL_ENABLED=false` to restore fixed-count legacy
evidence assembly. Existing hierarchical Documents remain searchable because legacy
retrieval still searches their child chunks.

Set `MIKURAG_CHUNKING_VERSION=legacy` to make future ingestion or retries use character
chunking. The retained `MIKURAG_CHUNK_TARGET_CHARACTERS` and
`MIKURAG_CHUNK_OVERLAP_CHARACTERS` settings control that rollback path. Existing
Documents are unchanged until re-ingested.

Set `MIKURAG_SUMMARY_GENERATION_ENABLED=false` to stop creating summaries during future
ingestion. Stored summaries remain internal and are never exposed as citation evidence.

Configuration validation rejects inconsistent token ranges, overlap greater than or
equal to the child minimum, parents no larger than children, evidence budgets that
cannot fit one maximum child, and merged passages larger than the total evidence
budget.

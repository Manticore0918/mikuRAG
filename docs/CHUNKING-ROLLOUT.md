# Hierarchical Chunking Rollout

The rollout remains controlled by environment feature flags while persistent
re-index jobs move existing Documents between chunking versions in bounded
batches. Only Administrators can read or mutate rollout state, and every mutation
requires CSRF protection.

## Phase sequence

1. **Phase 0 — baseline:** keep `CHUNKING_VERSION=legacy`, run the versioned
   retrieval evaluation set, capacity profiles, and structured observation
   collection.
2. **Phase 1 — structured chunking:** set
   `CHUNKING_VERSION=hierarchical_v1` for new ingestion while leaving
   `HIERARCHICAL_RETRIEVAL_ENABLED=false`.
3. **Phase 2 — retrieval expansion:** enable
   `HIERARCHICAL_RETRIEVAL_ENABLED=true` after candidate, reranker, evidence,
   citation, and latency metrics pass.
4. **Phase 3 — extraction quality:** canary repeated-furniture removal, layout
   warnings, OCR fallback, and table handling with representative files.
5. **Phase 4 — broad questions:** enable `SUMMARY_GENERATION_ENABLED=true` only
   after summary routing and provider cost are accepted.
6. **Phase 5 — re-indexing and default:** re-index a canary cohort, compare it
   with the baseline, expand to all Documents, then make the hierarchical flags
   the production defaults.

Feature-flag changes still require a service and worker restart. The status API
reports the currently configured phase, not an aspirational phase.

## Rollout API

All paths are below `/api/v1/admin/chunking-rollout`.

- `GET /status` returns active flags, the configured phase, document counts by
  stored chunking version, and active jobs.
- `POST /reindex-jobs` creates a canary or all-document job.
- `GET /reindex-jobs/{job_id}` returns durable progress counters.
- `POST /reindex-jobs/{job_id}/pause` stops claiming new batches.
- `POST /reindex-jobs/{job_id}/resume` restarts a paused job.
- `POST /reindex-jobs/{job_id}/cancel` cancels unclaimed items.
- `POST /reindex-jobs/{job_id}/rollback` creates a bounded legacy job for the
  exact successfully migrated cohort.

Example canary request:

```json
{
  "target_chunking_version": "hierarchical_v1",
  "selection_mode": "canary",
  "canary_percentage": 10,
  "batch_size": 5,
  "knowledge_base_id": null
}
```

An all-document job must explicitly set `selection_mode` to `all` and
`canary_percentage` to `100`. A new job is `queued`, becomes `running` only when
a worker claims it, and ends `completed` or `failed`. Only one queued, running,
or paused job is allowed globally, which prevents two jobs from replacing the
same Document chunks concurrently. Canary selection is deterministic and samples
each Knowledge Base separately.

## Worker behavior

The Celery task `mikurag.documents.reindex_batch` claims at most the job's
configured batch size using row locks with `SKIP LOCKED`. It then:

1. marks each claimed Document pending without deleting existing chunks;
2. runs ingestion with the job's explicit target version, independently of the
   default `CHUNKING_VERSION`;
3. atomically replaces chunks only after extraction, validation, and embedding
   succeed;
4. verifies that searchable child chunks use the requested version;
5. records a completed or failed per-document item;
6. refreshes durable job counters and queues the next bounded batch.

Pausing does not interrupt the already claimed batch. It prevents the next batch
from being claimed. Cancelling similarly leaves an in-flight batch to finish and
cancels only pending items.

If the queue becomes unavailable between batches, the job is paused with a safe
error and can be resumed. Failed Documents retain their previous chunks and remain
eligible for a later targeted retry, although their failed Document status keeps
them out of retrieval until retry succeeds.

If a worker stops after claiming an item, the durable `processing` claim is not
treated as success. After `REINDEX_STALE_AFTER_SECONDS`, another batch restores
the prior Ready state, requeues the item within `REINDEX_MAX_ATTEMPTS`, and starts
the requested version again. Exhausted items become failed and make the parent
job failed rather than leaving it running forever.

## Canary gates

Do not expand a canary until all of the following are reviewed against Phase 0:

- retrieval evaluation recall, MRR, citation accuracy, faithfulness, and complete
  supporting-passage rate;
- cross-page coverage and citation ranges;
- extraction warnings and failed ingestion rate;
- HNSW, lexical, reranker, retrieval, and end-to-end p50/p95 latency;
- worker memory, ingestion throughput, embedding requests, and storage growth;
- evidence-token and provider-cost growth.

Use `reindex_job_progress`, `document_ingestion`, and `retrieval_decision`
observation events for the operational comparison.

## Rollback

For a retrieval-only issue, first disable
`HIERARCHICAL_RETRIEVAL_ENABLED` and restart API instances; stored hierarchical
children remain compatible with the legacy retrieval path.

For a chunk-data issue:

1. pause or cancel any active expansion job;
2. restore `CHUNKING_VERSION=legacy` for new ingestion;
3. call the completed hierarchical job's `/rollback` endpoint;
4. monitor the generated legacy job to completion;
5. keep migration `0007` and hierarchical columns in place until rollback and
   incident analysis are complete.

Do not downgrade migration `0006` as an operational rollback. Its downgrade
deletes non-child hierarchical records and is intended only for controlled schema
reversal after data has already been restored.

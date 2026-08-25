# Hierarchical Chunking Acceptance Gates

The default rollout is controlled by a fail-closed, versioned acceptance report.
Every criterion in section 17 becomes one gate with one of three states:

- `pass`: measured evidence satisfies the configured threshold;
- `fail`: measured evidence violates the threshold;
- `not_measured`: required release or canary evidence is absent.

`ready_for_default_rollout` is true only when all ten gates pass. Missing evidence
never counts as success.

## Inputs

The release check consumes:

1. `evaluation_sets/retrieval_v1.json`;
2. legacy baseline observations for that exact evaluation-set version;
3. hierarchical candidate observations for the same version;
4. a `capacity_benchmark_v1` report from the standard or capacity profile;
5. an `acceptance_operational_v1` evidence file.

Copy
`backend/evaluation_sets/acceptance_operational_v1.example.json` into a release
evidence directory and replace each `null` only after its referenced regression or
canary check has run against the release commit.

Observation files use this shape:

```json
{
  "evaluation_set_version": "retrieval_v1",
  "observations": [
    {
      "case_id": "cross_page_access_rule",
      "retrieved_passage_ids": ["security-access-p14-15"],
      "reranked_passage_ids": ["security-access-p14-15"],
      "citation_pages": [14, 15],
      "answer_faithful": true,
      "retrieval_latency_ms": 125.4,
      "end_to_end_latency_ms": 980.2,
      "evidence_tokens": 840,
      "used_summary_path": false
    }
  ]
}
```

For whole-document summary cases, `used_summary_path` must be present and true.

## Running the gate

From `backend`:

```powershell
..\.venv\Scripts\python.exe -m app.acceptance_cli `
  --evaluation-set evaluation_sets/retrieval_v1.json `
  --baseline release-evidence/legacy-observations.json `
  --candidate release-evidence/hierarchical-observations.json `
  --benchmark release-evidence/capacity.json `
  --operational-evidence release-evidence/operational.json `
  --output release-evidence/acceptance-report.json
```

The command exits with status `0` only when default rollout is approved. A failed
or unmeasured gate exits with status `2`, making the command suitable for a
release workflow.

## Gate definitions

The report checks:

1. every cross-page case contains every required supporting passage;
2. every citable case has the exact expected page set;
3. property and hierarchy validation found no normalized source omission;
4. the configured maximum-page benchmark stays below
   `WORKER_MEMORY_LIMIT_BYTES`;
5. the mean of Recall@10, post-rerank recall, MRR, and all-required-passage rate
   improves by at least `ACCEPTANCE_MIN_QUALITY_IMPROVEMENT`, with no component
   regression;
6. every whole-document question records summary-path routing;
7. candidate retrieval p95 is at or below
   `ACCEPTANCE_RETRIEVAL_P95_TARGET_MS`;
8. average evidence tokens stay within `RETRIEVAL_EVIDENCE_TOKEN_BUDGET`;
9. idempotent re-ingestion and a real hierarchical-to-legacy rollback are both
   verified;
10. document deletion, document retry, and citation compatibility all pass.

Defaults are a 2-percentage-point minimum quality improvement, 1,500 ms retrieval
p95 target, and a 1 GiB worker memory limit. Configure these from measured product
requirements rather than relaxing them to make a release pass.

## Evidence ownership

Automated tests can prove deterministic coverage, chunk stability, API
compatibility, and local memory behavior. They cannot prove production HNSW
latency, provider behavior, or a live rollback. Those checks remain `not_measured`
until release/canary artifacts explicitly attest them.

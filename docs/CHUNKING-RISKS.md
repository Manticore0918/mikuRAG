# Hierarchical Chunking Risk Controls

Section 18 is maintained as a versioned machine-readable control register:

`backend/risk_register/chunking_risks_v1.json`

Each risk must have:

- at least one named control;
- live implementation paths;
- live regression-test references;
- structured monitoring signals;
- an explicit residual risk.

The verifier fails when a required risk disappears, an unexpected risk ID is
introduced without updating the schema, a control path is stale, or a referenced
test no longer exists.

## Verification

From `backend`:

```powershell
..\.venv\Scripts\python.exe -m app.risk_cli `
  --register risk_register/chunking_risks_v1.json `
  --repository-root ..
```

The command exits with status `0` only when all nine risk entries have live code
and regression evidence. This validates control traceability, not production
effectiveness; the monitoring signals and acceptance report remain necessary.

## Overload controls

Re-indexing has additional controls because it can amplify every ingestion cost:

- one active rollout job globally;
- configurable batch sizes from 1 to 100;
- sequential processing inside each claimed batch;
- `REINDEX_BATCH_DELAY_SECONDS` between batches;
- automatic retries only for `embed:` and `persist:` failures;
- `REINDEX_MAX_ATTEMPTS` as a hard retry cap;
- queue failures pause the job instead of spinning;
- Administrators can pause, resume, cancel, or roll back.

Defaults are three attempts and a two-second inter-batch delay. Increase pacing
before reducing batch size when provider request-rate limits are the bottleneck;
reduce batch size when worker memory or database write bursts are the bottleneck.

## Residual-risk review

Controls reduce probability or impact but do not eliminate the risks. Before
default rollout, review every register entry alongside:

- the release acceptance report;
- the standard and capacity benchmark reports;
- `document_ingestion`, `retrieval_decision`, and `reindex_job_progress` events;
- provider billing and throttling data;
- parser warnings grouped by media type and source producer.

Any new extraction provider, reranker, embedding model, chunking version, or
retrieval expansion mode must update the register and add test references before
deployment.

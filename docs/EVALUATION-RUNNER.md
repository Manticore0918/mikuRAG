# Executable evaluation runner

The checkpoint-2 runner executes evaluation against the real mikuRAG pipeline.
It does not accept pre-authored retrieval observations as proof of a run.

## Lifecycle

For every invocation, the runner:

1. loads and validates a versioned manifest under `backend/evaluation/corpus/`;
2. creates a uniquely named, isolated evaluation Knowledge Base;
3. copies every corpus source into managed storage and creates Pending Documents;
4. sends the normal `mikurag.documents.ingest` tasks to Redis/Celery;
5. polls PostgreSQL until every Document is Ready or Failed;
6. embeds each query and calls the production hybrid retriever;
7. optionally calls the production grounded generation and validation path;
8. writes a raw run record, JSON aggregate, and Markdown report; and
9. deletes the isolated Knowledge Base, chunks, and stored source files by default.

Provider keys are never written to evaluation artifacts. Reports record public
configuration identifiers such as model IDs, the chunking profile, and the
canonical chunking configuration hash.

## Run

Start PostgreSQL, Redis, and the worker, then run:

```powershell
.\scripts\mikurag.ps1 evaluate
```

```sh
sh ./scripts/mikurag.sh evaluate
```

The compose `evaluate` service runs the `run` subcommand. To execute a specific
chunker profile, a smoke subset, or the grounded answer path directly:

```powershell
docker compose --profile tools run --rm evaluate python -m app.evaluation_cli run --dataset evaluation/corpus/gold_v1/manifest.json --chunking-version token_recursive_v1
docker compose --profile tools run --rm evaluate python -m app.evaluation_cli run --max-cases 5 --answers
```

`run` accepts `--chunking-version`, `--answers`, `--keep-knowledge-base`,
`--timeout-seconds`, `--poll-seconds`, `--run-id`, `--max-cases`, and
`--bootstrap-samples`/`--bootstrap-seed`. Use `--keep-knowledge-base` only for
manual debugging. The default cleanup makes repeat runs independent and prevents
evaluation Documents from entering normal application retrieval.

Checkpoint 3 adds the retrieval experiment knobs to `run` and `compare`:

- `--retrieval-mode {vector,fts_baseline,bm25,hybrid_rrf,hybrid_rrf_reranked}`
  selects which retrieval legs and post-processing run (default: the configured
  `MIKURAG_RETRIEVAL_MODE`);
- `--reranker {deterministic,cross_encoder}` selects the reranker used by
  reranked modes (default: the configured provider);
- `--bm25-hybrid-enabled` forces the pg_search BM25 leg into hybrid modes for
  this run, even when `MIKURAG_BM25_HYBRID_ENABLED` is off (the production
  default keeps BM25 off until the evaluation gate passes);
- `--no-query-planning` disables the typed query plan per evaluation case. The
  evaluation switch is explicit and independent of the production
  `MIKURAG_QUERY_PLANNING_ENABLED=false` default: each manifest case can carry a
  bounded synthetic Conversation history, so enabled evaluation cases exercise
  the real typed rewrite path and record the original/effective query, rewrite
  status, preserved identifiers, and history in `raw-run.json`.

### Retrieval modes

Every mode pushes the authorization scope (Knowledge Base membership, Ready
status, embedding model) and any metadata filters into the retrieval SQL before
candidate limits, per the delivery rule that filters precede ranking:

| Mode | Semantic leg | Lexical leg | Post-processing |
| --- | --- | --- | --- |
| `vector` | pgvector cosine | — | weighted RRF (single leg) |
| `fts_baseline` | — | PostgreSQL FTS | weighted RRF (single leg) |
| `bm25` | — | pg_search BM25 (FTS fallback) | weighted RRF (single leg) |
| `hybrid_rrf` | pgvector cosine | FTS (BM25 when enabled) | weighted RRF |
| `hybrid_rrf_reranked` | pgvector cosine | FTS (BM25 when enabled) | weighted RRF then reranker |

The `bm25` mode falls back to PostgreSQL FTS when the `pg_search` extension or
index is unavailable, unless `MIKURAG_BM25_FALLBACK_TO_FTS=false`. Hybrid modes
keep the FTS baseline until `MIKURAG_BM25_HYBRID_ENABLED=true` (off by default
per the delivery rule). The reranker is the local cross-encoder when
`--reranker cross_encoder`; it reranks only the fused top-N under batch,
timeout, and concurrency limits, and falls back to fused order on any failure,
so a failing reranker never corrupts a run.

## Compare

The `compare` subcommand is the checkpoint-2 exit gate: it runs every chunking
profile (`legacy_char_v1`, `token_recursive_v1`, `hierarchical_v1`) against the
same corpus, then produces quality, latency, token, and storage results with a
per-candidate acceptance verdict against the existing acceptance report:

```powershell
docker compose --profile tools run --rm evaluate python -m app.evaluation_cli compare --dataset evaluation/corpus/gold_v1/manifest.json --split test
```

`compare` accepts `--profiles`, `--baseline`, `--split {all,train,dev,test}`,
`--answers`, `--max-cases`, and `--bootstrap-samples`/`--bootstrap-seed`. The
headline metrics and acceptance gate default to the untouched `test` split, per
the tuning rule (tune on train/dev; publish from test). The result is written to
`backend/evaluation/results/compare/<version>/<split>/compare.json` (machine
readable) and `compare.md` (a committed summary with the per-category winner
table). A candidate is `ready_for_default_rollout` only when no measured
acceptance gate fails: cross-page context, citation ranges, composite retrieval
quality improvement without component regression, the 1,500 ms retrieval p95,
and the evidence-token budget.

## Ablation

The `ablation` subcommand is the checkpoint-3 mechanism for the component
experiments: it runs every requested retrieval mode against the same corpus and
publishes a per-config metrics table with real numbers (no placeholders):

```powershell
docker compose --profile tools run --rm evaluate python -m app.evaluation_cli ablation --dataset evaluation/corpus/gold_v1/manifest.json --split test
```

To ablate BM25 value vs the vector baseline with the learned reranker on top:

```powershell
docker compose --profile tools run --rm evaluate python -m app.evaluation_cli ablation --dataset evaluation/corpus/gold_v1/manifest.json --modes vector bm25 hybrid_rrf_reranked --reranker cross_encoder --split test
```

`ablation` accepts `--modes` (default: all five experiment modes), `--reranker`,
`--bm25-hybrid-enabled`, `--split {all,train,dev,test}` (default `test`), and
`--query-planning {both,on,off}` (default `both`) plus
`--max-cases`/`--bootstrap-samples`/`--bootstrap-seed`. With `both`, every mode
runs once with the typed rewrite plan and once with the original query. Each
configuration runs through the full ingestion + retrieval lifecycle against the
frozen corpus split, so the table reports Recall@10, MRR@10, NDCG@10, the
retrieval p95 in milliseconds, and mean evidence tokens per config. It also
records the effective lexical leg and reranker provider observed in the raw case
records. A requested BM25 row that used FTS fallback, or a learned-reranker row
that fell back to fused order, is marked invalid for headline comparison rather
than being mislabeled as a successful component run. The result is written to
`backend/evaluation/results/ablation/<version>/<split>/ablation.json` (machine
readable) and `ablation.md` (a committed table). The output is the input to the
component decision: enable only components that improve the frozen test set
inside the latency and evidence-token budget.

## Artifacts

Each run writes to
`backend/evaluation/results/<evaluation-set-version>/<run-id>/`:

- `raw-run.json`: immutable run metadata (including the chunking configuration
  hash and ingestion statistics), durable Document states, per-case retrieved
  evidence and locators, timings, optional answers, and safe failures;
- `report.json`: aggregate metrics, per-category and per-split metrics, and
  bootstrap confidence intervals; and
- `report.md`: a compact human-readable summary covering quality, Ingestion
  time, chunk count, embedding inputs, storage estimate, retrieval latency, and
  evidence tokens.

Failed ingestion or provider runs still write the available raw state and report
before returning a non-zero exit code.

## Corpus schema

`manifest.json` schema version 1 contains redistribution metadata, Documents,
and cases. Schema version 2 (the `gold_v1` corpus) additionally declares a
`review_status` of `reviewed`, a train/dev/test `split` per case, graded
`relevance_grades` (required passages graded 3, other relevant passages graded
1), and per-passage `locator_match` objects for deterministic passage-to-chunk
resolution. The corpus must declare its license/provenance, reference a
non-empty license file, and explicitly declare that it contains no sensitive
data. Each Document has a stable lowercase `document_id`, `passage_id`,
`locator_id`, and a corpus-relative source path. The runner copies the passage
and locator IDs into the non-secret chunk provenance allowlist as
`source_passage_id` and `source_locator_id`, so raw run records can address the
exact gold passage and source locator independently of database UUIDs,
filenames, or hand-authored observations.

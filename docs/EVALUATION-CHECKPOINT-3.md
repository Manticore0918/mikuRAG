# Checkpoint 3 retrieval evaluation

Measured on 2026-08-28 with evaluation set `gold_v1`. The executable runner
created a separate Knowledge Base for every configuration, ingested all 14
versioned corpus Documents through the worker, observed 14/14 terminal `Ready`
states, executed all 64 reviewed cases, wrote raw and aggregate artifacts, and
removed the isolated Knowledge Base. Headline metrics use the frozen 13-case
`test` split; the other cases remain available for train/dev diagnosis.

The database runtime was `paradedb/paradedb:0.25.5-pg16` (PostgreSQL 16.15,
pg_search 0.25.5). BM25 and hybrid rows below recorded only the effective
lexical provider `bm25`; no PostgreSQL FTS fallback occurred. Query planning was
off for this retrieval-mode matrix. The grounded answer path was intentionally
disabled because answer faithfulness is checkpoint 4.

## Frozen test-split ablation

| Configuration | Effective providers | Recall@10 (95% CI) | MRR@10 (95% CI) | NDCG@10 (95% CI) | Steady-state p95 retrieval | Evidence tokens | Headline valid |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| Vector only | vector | 0.8846 [0.7308, 1.0000] | 0.7500 [0.5192, 0.9423] | 0.8408 [0.6560, 1.0000] | 15.6 ms | 290 | no |
| PostgreSQL FTS baseline | FTS | 0.0769 [0.0000, 0.2308] | 0.0769 [0.0000, 0.2308] | 0.0769 [0.0000, 0.2308] | 7.4 ms | 0 | no |
| BM25 only | pg_search BM25 | 0.9487 [0.8462, 1.0000] | 0.8654 [0.6538, 1.0000] | 0.8922 [0.7746, 0.9885] | 29.3 ms | 272 | no |
| Vector + BM25 + RRF | vector, BM25 | 0.9615 [0.8846, 1.0000] | 0.7949 [0.6026, 0.9615] | 0.8871 [0.7667, 1.0000] | 50.6 ms | 300 | no |
| Hybrid + local cross-encoder | vector, BM25, `ms-marco-MiniLM-L-6-v2` | 0.9487 [0.8462, 1.0000] | 0.8333 [0.6407, 1.0000] | 0.9107 [0.7829, 1.0000] | 287.8 ms | 267 | no |

`gold_v1` remains `headline_eligible: false`, so these rows are measured
diagnostics rather than publishable headline claims. The intervals above are
bootstrap intervals recomputed on the same frozen 13-case test split with the
recorded 1,000 samples and seed `20260828`.

The reranker row requires a latency correction. The original runner loaded the
cross-encoder during the first timed test case. That case measured 11,836.3 ms
retrieval / 11,799.6 ms reranking; the other 12 cases had a 182.4 ms median and
287.8 ms p95. With only 13 cases, interpolation against that one cold outlier
produced the original cold-inclusive 4,914.3 ms p95. The evidence envelope keeps
both values. Current runs prewarm the reranker outside timed cases and record
the warmup duration separately.

Run IDs, in table order:

- `vector-original-20260828T073111Z-93f873`
- `fts_baseline-original-20260828T073129Z-03f628`
- `bm25-original-20260828T073147Z-7f3d18`
- `hybrid_rrf-original-20260828T073207Z-056039`
- `hybrid_rrf_reranked-original-20260828T073228Z-d4b64b`

The versioned raw records and generated reports are written locally under
`backend/evaluation/results/gold_v1/<run-id>/`. The generated comparison is
`backend/evaluation/results/ablation/gold_v1/test/ablation.{json,md}`. These
runtime artifacts are ignored by Git; this document is the redistributable
result summary.

## Decisions

- BM25 beat vector-only on the frozen test split by 0.0641 Recall@10, 0.1154
  MRR, and 0.0514 NDCG@10 for 13.8 ms additional p95 retrieval latency.
- Hybrid RRF produced the best recall (0.9615). Against BM25-only it added
  0.0128 recall but reduced MRR by 0.0705 and NDCG by 0.0051, so the current
  equal RRF weights are retained as an experiment rather than made the default.
- The local cross-encoder improved hybrid MRR by 0.0385 and NDCG by 0.0236, but
  reduced recall by 0.0128 and raised steady-state p95 from 50.6 ms to 287.8 ms.
  Its original CPU cold start was 11.8 seconds. The learned reranker remains
  disabled by default pending larger-corpus and concurrency evidence; its
  timeout, concurrency limit, and fused-order fallback stay available and tested.
- Metadata-filtered cases recorded filter correctness 1.0000. Authorization is
  a separate mandatory pre-ranking predicate, with cross-Knowledge-Base leakage
  tests covering every mode.
- Category diagnostics did not support the original hypothesis that BM25 would
  specifically beat vector retrieval on exact identifiers: across all eight
  exact-identifier cases both reached 1.0000 required-passage recall, while
  vector MRR was 1.0000 and BM25 MRR was 0.9375. Semantic-paraphrase cases were
  complementary: vector recall/MRR was 0.8571/0.8571, BM25 was
  1.0000/0.7786, and hybrid was 1.0000/0.9048. Because the category-specific
  BM25 gate is not met, `MIKURAG_BM25_HYBRID_ENABLED` remains false by default
  despite BM25's aggregate test-split improvement.
- Disabling pg_search or the learned reranker still selects the working
  pgvector + PostgreSQL FTS + RRF path without re-ingestion.

## Query-planning ablation

The typed query-planning run
`vector-rewritten-20260828T073512Z-8ba173` completed all 64 cases. The
deterministic eligibility rule left 61 questions unchanged; all three eligible
rewrite requests hit the provider timeout/unavailable path and were recorded as
`rewrite_failed`, then safely used the original question. Test retrieval metrics
therefore matched vector-original (0.8846 Recall@10, 0.7500 MRR, 0.8408
NDCG@10) while p95 retrieval increased to 25.1 ms. Production query planning is
disabled by default with `MIKURAG_QUERY_PLANNING_ENABLED=false` until a run
contains successful rewrites and demonstrates a frozen-test lift. Evaluation
flags continue to control planning explicitly and independently of that default.

The paired CLI invocation subsequently encountered a transient embedding
provider connection failure while starting its second, original-query run. It
did not overwrite or invalidate the completed five-mode matrix above.

## Reproduction

```powershell
docker compose --profile tools run --rm evaluate python -m app.evaluation_cli ablation `
  --modes vector fts_baseline bm25 hybrid_rrf hybrid_rrf_reranked `
  --reranker cross_encoder --bm25-hybrid-enabled --split test `
  --query-planning off --timeout-seconds 180 --poll-seconds 1 `
  --bootstrap-samples 1000 --bootstrap-seed 20260828
```

Before the run, execute `scripts/spike_pg_search.ps1` and the normal `migrate`
service. The spike validates the pinned image, extensions, index dialect,
apostrophe-safe typed matching, and scoring; migration reconciles the extension
and index for existing volumes.

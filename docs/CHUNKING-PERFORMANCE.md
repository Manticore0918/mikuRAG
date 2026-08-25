# Chunking Performance and Capacity Benchmarks

Section 15 is implemented as a repeatable synthetic benchmark plus production
timing fields. The synthetic suite does not call embedding providers or require a
database, so it is safe to run locally and in CI. Production canary runs provide
the database and provider measurements that synthetic data cannot represent.

## Running the suite

Run commands from `backend`:

```powershell
..\.venv\Scripts\python.exe -m app.benchmark_cli --profile smoke
..\.venv\Scripts\python.exe -m app.benchmark_cli --profile standard --output benchmark.json
..\.venv\Scripts\python.exe -m app.benchmark_cli --profile capacity --max-pages 500 --output capacity.json
```

- `smoke` is a fast correctness and schema check.
- `standard` covers 10, 50, 200, and configured maximum-size documents,
  concurrent jobs, a multi-document knowledge base, and cold/warm retrieval.
- `capacity` uses larger blocks, eight ingestion jobs, fifty 200-page documents,
  and twenty warm retrieval runs. Run it on a worker-sized host before rollout.

The report schema is versioned as `capacity_benchmark_v1`. Keep reports from the
legacy and hierarchical implementations under the same hardware and configuration
when comparing them.

## Measurements

The ingestion results report:

- synthetic extraction, normalization, chunk construction, and total time;
- traced peak worker memory and peak-memory/source-byte amplification;
- parent, child, and embedding counts;
- embedding-input token count;
- estimated text, metadata, and 768-dimensional vector storage.

The concurrent result reports aggregate chunk counts and document throughput. The
knowledge-base result reports aggregate pages, chunks, embeddings, estimated
storage, and elapsed time. Retrieval reports cold latency, warm mean and p95
latency, reranker latency, selected evidence count, and evidence-token growth.

The CI regression test checks that maximum-document memory remains approximately
linear relative to the 50-page case and caps traced memory amplification. Do not
replace these guards with exact latency thresholds: shared CI timing is noisy.

## Production-only measurements

Every `retrieval_decision` observation now includes:

- `semantic_query_duration_ms` for the pgvector/HNSW query;
- `lexical_query_duration_ms` for PostgreSQL full-text search;
- `reranking_duration_ms`;
- `retrieval_duration_ms`;
- `evidence_token_count`.

Every `document_ingestion` observation includes separate extraction,
normalization, chunk-construction, validation, embedding, persistence, and total
durations, along with chunk counts and token distributions.

For cold retrieval, run the representative evaluation set after a database restart
or cache eviction. For warm retrieval, repeat the same set without restarting.
Capture p50/p95 latency from the structured observations. Also record worker RSS
outside Python: `tracemalloc` measures Python allocations but not native parser,
database-driver, or provider-client memory.

## Acceptance review

Before enabling hierarchical chunking by default, compare legacy and hierarchical
reports for:

- retrieval quality metrics from `retrieval_v1`;
- peak worker RSS and traced memory;
- extraction, HNSW, lexical, reranking, and end-to-end latency;
- chunk, embedding, storage, and evidence-token growth;
- concurrent ingestion throughput and failure rate.

Investigate non-linear memory growth, provider batch explosions, database latency
regressions, or evidence growth beyond the configured token budget before rollout.

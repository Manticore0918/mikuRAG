# mikuRAG Portfolio Roadmap

## Goal

Turn mikuRAG into a strong second job-application project: a production-minded,
self-hosted RAG system that demonstrates document engineering, information
retrieval, evaluation, backend architecture, and operations—not merely a chat UI
connected to a model.

The core portfolio story should be:

> mikuRAG ingests heterogeneous private Documents asynchronously, compares
> versioned chunking and retrieval strategies, retrieves with dense vectors plus
> true BM25 and reciprocal-rank fusion, validates Citations, and publishes
> reproducible quality/latency/cost results. It runs locally with Docker and has
> CI, release automation, caching, and end-to-end observability.

This plan was written against the current working tree on 2026-08-24, including
the uncommitted hierarchical-chunking, evaluation, rollout, and observability
work. Those changes belong to the existing work and must be consolidated rather
than overwritten.

## Delivery rules

Every checkpoint must leave the current product usable without any later
checkpoint:

1. Database migrations are forward-safe and have a tested rollback or compatible
   feature-off path.
2. New retrieval behavior is disabled by default until its evaluation gate passes.
3. PostgreSQL remains authoritative; Redis and any optional graph index contain
   rebuildable derived data only.
4. A failed cache, reranker, evaluator, or optional service must not corrupt
   Ingestion or prevent the stable retrieval path from operating.
5. Every experiment records dataset version, git SHA, chunking configuration,
   embedding model, retrieval configuration, and timing information.
6. Authorization and Knowledge Base filters are applied before ranking, never as
   post-retrieval cleanup.
7. No Document text, query text, evidence text, credentials, or personal data is
   emitted in telemetry.

## Current-state audit

| Capability | Current state | Work still needed |
| --- | --- | --- |
| FastAPI backend | Implemented with management, auth, health, upload, Conversation, Citation, and rollout routes | Preserve and add API contracts for filters, experiments, and metrics |
| Docker | Backend/frontend Dockerfiles and Compose services for PostgreSQL, Redis, API, worker, beat, and frontend | Add automated Compose smoke test and versioned release images |
| PostgreSQL + pgvector | Implemented; chunks have 768-dimensional vectors and an HNSW index | Keep as the primary data/retrieval store; benchmark index settings with real data |
| Redis | Used for Celery, health, and login throttling | Add bounded, versioned query-embedding and retrieval caches |
| Async/background Ingestion | Implemented with Celery, durable Document states, retry/delete, resumable uploads, and in-progress batch re-indexing | Prove crash recovery and expose useful job progress/attempt data |
| Document formats | PDF, DOCX, TXT, and Markdown implemented | Add HTML and code; preserve DOM/symbol/line provenance |
| Chunking | Legacy character splitter plus feature-flagged `hierarchical_v1`, token-aware parent/child construction, validation, rollout, and rollback work | Turn configurations into repeatable experiments; publish measured comparisons |
| Vector retrieval | Implemented with pgvector cosine distance | Add explicit vector-only experiment mode and tune from evaluation evidence |
| Lexical retrieval | PostgreSQL FTS with `websearch_to_tsquery` and `ts_rank_cd` | This is not BM25; add a true BM25 implementation behind an adapter |
| Hybrid retrieval + RRF | Implemented; semantic and lexical candidates are fused | Add ablations, weighted/configurable experiments, and regression gates |
| Reranking | Provider interface and deterministic lexical-overlap reranker exist | Add a real local cross-encoder provider with timeout/fallback behavior |
| Metadata filtering | Knowledge Base, Ready state, chunk level, and embedding-model filters exist | Add user-visible Document/tag/type/language/date filters pushed into both retrievers |
| Citations/source tracing | Strong baseline: server-owned Citation markers, retained excerpts, page ranges, source endpoint, and frontend formatting | Add HTML DOM locators and code path/symbol/line-range locators; measure Citation correctness |
| Query rewriting | Implemented for Conversation follow-ups with token usage captured | Preserve identifiers/filters, add failure fallback, and evaluate raw vs rewritten queries |
| Retrieval evaluation | Versioned eight-case manifest, metric functions, observation loader, benchmark scaffolding, and rollout gates exist | Build an executable corpus/runner; current code mainly scores supplied observations |
| Recall/MRR/NDCG | Recall@10, post-rerank recall, and MRR exist | Add Recall@1/5, NDCG@10 with graded relevance, per-category results, and confidence intervals |
| Groundedness/faithfulness | Structured generation and strict validation prevent unknown Citation IDs | Validation proves Citation membership, not semantic claim support; add claim-level evaluation |
| Latency/tokens/cost | Structured ingestion/retrieval durations and generation token usage exist | Add end-to-end spans, p50/p95/p99 reports, embedding tokens, and a versioned cost ledger |
| Observability | JSON observation log lines exist | Export metrics and traces; add correlation IDs, dashboards, and alert/SLO examples |
| CI/CD | Local commands exist; no `.github/workflows` directory | Add pull-request CI and tag-based container release automation |
| Neo4j/GraphRAG | Not present | Optional only after the core evaluation path is credible |
| Agentic retrieval | Broad-query classification and hierarchical summaries are an early foundation | Optional bounded query decomposition after core quality/latency gates pass |

### Verified baseline

The current working tree passed these checks during the audit:

- Backend: 137 tests passed.
- Backend Ruff: passed.
- Frontend: 5 files / 8 tests passed.
- Frontend ESLint: passed.
- Frontend production build: passed.

These counts are a snapshot, not permanent acceptance thresholds. CI should run
the commands rather than assert fixed test counts.

## Architecture decisions

### Keep PostgreSQL + pgvector

Do not add Qdrant merely to list another technology. mikuRAG already has a sound
PostgreSQL/pgvector model, transactional Ingestion, authorization-scoped joins,
and an accepted ADR. A second vector source would add dual-write and deletion
consistency problems without yet solving a measured limitation.

For true BM25, introduce a `LexicalRetriever` boundary and prefer a PostgreSQL
BM25 extension so filters, ownership, and indexed content stay close to the
authoritative records. The recommended spike is `pg_search`; the ParadeDB image
packages both `pg_search` and `pgvector`. Keep current PostgreSQL FTS as a
portable fallback until BM25 passes integration and evaluation gates.

Reference: [ParadeDB extension documentation](https://docs.paradedb.com/deploy/third-party-extensions).

Reconsider Qdrant only if a measured requirement cannot be met by the PostgreSQL
path—for example, BM25-extension deployment is unacceptable or vector scale is
proven to exceed the agreed capacity envelope. If reconsidered, write an ADR and
benchmark the adapter against the same frozen evaluation set.

### Treat evaluation as a product feature

The evaluation runner and reports are part of the repository, versioned and
repeatable. Retrieval changes do not become defaults because they “look better”
on a few questions. They become defaults only after a frozen test split shows a
quality improvement inside latency, token, and cost budgets.

### Keep advanced paths optional

Neo4j, GraphRAG, and agentic decomposition must use Compose profiles and feature
flags. The normal PDF/HTML/Markdown/code-to-answer flow must not require them.

## Checkpoint 0 — Consolidate a shippable baseline

### Outcome

A tagged baseline that a reviewer can clone, start, and use end to end. The
in-progress hierarchical work is integrated safely but remains feature-off by
default until measured.

### Work

- Review and split the current large working tree into understandable commits:
  schema, extraction/normalization, hierarchical chunking, retrieval, evaluation,
  rollout, frontend Citation compatibility, and documentation.
- Verify migrations `0006` and `0007` from a clean database and from the previous
  schema. Test the compatibility path with `legacy` chunking and hierarchical
  retrieval disabled.
- Finish the re-index job lifecycle: queued/running/paused/completed/failed,
  bounded retries, idempotency, and rollback to legacy chunks.
- Add a deterministic demo seed containing a small PDF and Markdown Document plus
  questions that exercise exact matching, paraphrase, follow-up rewriting,
  Citations, insufficient evidence, and authorization.
- Add one cross-platform entry point (task runner or paired PowerShell/shell
  scripts) for setup, checks, seed, and smoke test.
- Correct README status language so it distinguishes stable defaults from
  experimental, feature-flagged work.
- Tag the result as the first reproducible portfolio baseline.

### Exit gate

- A clean clone can migrate, start with Docker Compose, create an Administrator,
  ingest the seed Documents in the worker, and answer the seed questions with
  inspectable Citations.
- Restarting the API during upload and restarting a worker during Ingestion do not
  expose partial chunks or leave a Ready Document with incomplete evidence.
- Backend tests/lint and frontend tests/lint/build pass.
- Both feature-off and feature-on hierarchical paths have focused smoke tests.

### Portfolio proof

Commit a short baseline demo script and a screenshot/GIF showing upload status,
background Ingestion, a Grounded Answer, and expandable Citations.

## Checkpoint 1 — Heterogeneous Ingestion and provenance

### Outcome

mikuRAG can ingest PDF, HTML, Markdown, and a deliberately scoped set of code
formats asynchronously, and every retrieved passage has a source-specific
locator. The app remains fully useful if later retrieval work is never added.

### Work

- Create an extractor registry/protocol instead of continuing media-type branches
  in `extract_document`.
- Preserve the current PDF/DOCX/TXT/Markdown adapters and add:
  - HTML: remove scripts/styles/navigation noise, preserve title, heading path,
    DOM selector or element path, canonical/source URL when supplied, and text
    offsets.
  - Code: begin with Python and TypeScript/JavaScript; preserve repository-relative
    path, language, symbol/module name when known, and start/end lines. Use a
    generic line-aware fallback for declared text source files.
- Add parser version, source kind, language, tags, source URI/path, and arbitrary
  validated metadata to the Document model. Copy only retrieval-relevant,
  non-secret fields to chunks.
- Add MIME/extension allowlists, encoding handling, zip-bomb/size protections where
  applicable, and safe errors for malformed HTML or source files.
- Extend Citation locators and frontend rendering:
  - PDF: `p. 14` or `pp. 14–15`.
  - HTML/Markdown: heading and element/line locator.
  - Code: `path/to/file.py:40` plus symbol when available.
- Expose Ingestion stage, progress, attempt count, parser/chunker version, and safe
  warnings in the Administrator UI.
- Add fixture and integration tests for each source type, deletion, retry,
  duplicate upload, parser failure, and Citation source lookup.

### Likely code boundaries

- `backend/app/ingestion/extractors/`
- `backend/app/ingestion/contracts.py`
- `backend/app/ingestion/normalization.py`
- `backend/app/ingestion/tasks.py`
- `backend/app/models.py` and a new Alembic migration
- `backend/app/rag/citations.py`
- `frontend/src/DocumentPanel.tsx` and Citation formatting

### Exit gate

- One Knowledge Base containing PDF, HTML, Markdown, Python, and TypeScript
  Documents can be queried after background Ingestion.
- Citations open the correct page, heading/element, or path/line range.
- An API/worker restart and a failed parser leave the Installation consistent.
- Existing formats and legacy chunking still pass their regression suite.

### Portfolio proof

Publish a provenance table in the README showing each source type, extracted
structure, stored locator, and rendered Citation.

## Checkpoint 2 — Executable evaluation and chunking laboratory

### Outcome

The repository can ingest a frozen evaluation corpus, execute actual retrieval,
compare chunking strategies, and generate machine-readable plus human-readable
reports. The normal application still uses the proven default configuration.

### Work

- Replace observation-only evaluation with an executable runner that:
  1. creates an isolated evaluation Knowledge Base;
  2. ingests the versioned corpus through the real worker/pipeline;
  3. waits for terminal Document states;
  4. executes the real retriever and optional answer path;
  5. writes raw run records and aggregate reports.
- Store a redistributable, non-sensitive corpus in
  `backend/evaluation/corpus/<version>/` with stable passage/locator IDs.
- Grow the gold set from the current eight scenarios to at least 60 carefully
  reviewed questions before using it for headline results. Cover:
  - exact identifiers, numbers, and error codes;
  - semantic paraphrases;
  - cross-page and cross-section evidence;
  - code symbol/behavior questions;
  - HTML heading and list questions;
  - multi-Document comparisons;
  - metadata-filtered questions;
  - broad questions;
  - unsupported and conflicting-evidence questions.
- Use train/dev/test splits. Tune on train/dev; publish final numbers from the
  untouched test split.
- Add graded qrels and calculate:
  - Recall@1, Recall@5, and Recall@10;
  - MRR@10;
  - NDCG@10;
  - all-required-passages rate;
  - filter correctness and Citation locator accuracy;
  - per-category results, not only global means.
- Add bootstrap confidence intervals where the dataset size supports them. Do not
  claim an improvement when intervals are too wide to distinguish configurations.
- Introduce a `Chunker` interface and versioned profiles, initially:
  - `legacy_char_v1`;
  - `token_recursive_v1`;
  - `hierarchical_v1`.
- Persist the full chunk configuration/hash on each indexed version and in every
  evaluation run. Do not use a single mutable global splitter setting as the
  experiment identity.
- Produce `JSON` raw results and a committed Markdown summary with quality,
  Ingestion time, chunk count, embedding inputs, storage estimate, retrieval
  latency, and evidence tokens.

### Likely code boundaries

- Refactor `backend/app/rag/evaluation.py` into
  `backend/app/evaluation/{datasets,runner,metrics,reporting}.py`.
- Keep `backend/app/benchmarking.py` for capacity work and connect its report IDs
  to evaluation runs.
- Add `backend/app/evaluation_cli.py` and versioned result schemas.
- Add a small smoke subset for CI; keep the full provider-backed suite scheduled
  or manually triggered.

### Exit gate

- One command compares all three chunking profiles against the same corpus and
  produces Recall/MRR/NDCG, latency, token, and storage results.
- Re-running an identical configuration produces the same chunks and equivalent
  deterministic retrieval metrics.
- No candidate becomes the default unless it clears the existing acceptance
  report, including the 1,500 ms retrieval p95 and evidence-token budget.

### Portfolio proof

Commit a concise experiment report with a table explaining which chunker wins by
question category and why. This report is more valuable to recruiters than a
claim that “advanced chunking” exists.

## Checkpoint 3 — True BM25, hybrid retrieval, filters, rewriting, and reranking

### Outcome

A measurable retrieval stack with vector-only, BM25-only, and hybrid modes;
reciprocal-rank fusion; pre-ranking metadata filters; and a real reranker with a
safe fallback.

### Work

- Introduce explicit boundaries:
  - `VectorRetriever`;
  - `LexicalRetriever`;
  - `FusionStrategy`;
  - `Reranker`;
  - `RetrievalPlan`/`RetrievalFilters`.
- Keep the current pgvector retriever and current PostgreSQL FTS retriever as the
  compatibility baseline.
- Run a short `pg_search` compatibility spike, record an ADR, pin a tested database
  image/extension version, create the BM25 index in a migration, and add readiness
  checks. If it fails operational requirements, retain the interface and make a
  measured Qdrant sparse+dense adapter the explicit alternative—not an
  unplanned second index.
- Add experiment modes: `vector`, `fts_baseline`, `bm25`, `hybrid_rrf`, and
  `hybrid_rrf_reranked`.
- Keep RRF as the safe score-scale-independent fusion baseline. Make candidate
  counts, RRF constant/weights, and evidence limits part of the versioned run
  configuration.
- Add API/UI filters for Document IDs, tags, source type, language, and ingestion
  date. Normalize one filter object and push it into vector and BM25 SQL before
  candidate limits are applied.
- Preserve authorization filters as mandatory and separate from user-selectable
  filters. Add cross-Knowledge-Base leakage tests for every retriever mode.
- Add a local cross-encoder reranker provider behind the existing interface:
  - rerank only the fused top-N candidates;
  - batch inference;
  - enforce timeout and concurrency limits;
  - fall back to fused order on provider failure;
  - record provider/model/version and latency.
- Keep `DeterministicReranker` as a test/fallback implementation, not the headline
  reranker.
- Promote query rewriting to a typed query plan containing original query,
  rewritten query, inferred filters, preserved identifiers, and rewrite status.
  Fall back to the original query on invalid/slow rewriting.
- Run ablations for original vs rewritten query, each retrieval leg, RRF, and
  reranking. Enable only components that improve the frozen test set inside the
  latency budget.

### Exit gate

- Exact-code questions demonstrate BM25 value; paraphrase questions demonstrate
  vector value; hybrid does not regress either category beyond the documented
  tolerance.
- Reranking improves MRR/NDCG on the untouched test split and has a tested
  timeout/fallback path.
- Metadata filters return only allowed matching Documents in all modes.
- Disabling BM25 or the learned reranker returns to a working pgvector + FTS + RRF
  path without re-uploading source Documents.

### Portfolio proof

Publish an ablation table:

| Configuration | Recall@10 | MRR@10 | NDCG@10 | p95 retrieval | Evidence tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Vector only | measured | measured | measured | measured | measured |
| BM25 only | measured | measured | measured | measured | measured |
| Vector + BM25 + RRF | measured | measured | measured | measured | measured |
| Hybrid + reranker | measured | measured | measured | measured | measured |

Do not fill this table with synthetic placeholder numbers.

## Checkpoint 4 — Answer faithfulness, cost accounting, and Redis caching

### Outcome

mikuRAG can show whether answers are actually supported, what each stage costs,
and how caching changes cold/warm latency without weakening privacy or
correctness.

### Work

- Extend the evaluation dataset with expected claims, acceptable answer facts,
  required evidence, refusal expectation, and conflicting-evidence cases.
- Add deterministic answer metrics first:
  - Citation precision and recall;
  - claim Citation coverage;
  - unsupported-Citation rate;
  - refusal correctness;
  - answer completeness against expected facts.
- Add a versioned claim-support/faithfulness evaluator. If an LLM judge is used,
  pin the judge model and prompt, store its rationale separately from the score,
  calibrate it against a human-reviewed subset, and never send private evaluation
  data to an undisclosed provider.
- Report groundedness/faithfulness per category and include judge disagreement or
  human-audit rate. Do not collapse retrieval failure and generation
  hallucination into one score.
- Add an end-to-end measurement record for each turn:
  - rewrite, query embedding, vector, BM25, fusion, rerank, expansion, generation,
    validation, persistence, and total latency;
  - prompt/completion/embedding tokens;
  - evidence tokens and retrieved candidate counts;
  - cache hit/miss;
  - model/provider versions;
  - estimated cost from a versioned pricing table. Local generation is recorded
    as zero API spend, not as zero compute cost.
- Report p50/p95/p99 for retrieval and end-to-end latency, plus Ingestion
  throughput and embedding volume.
- Add Redis as an optional derived-data cache:
  - query embeddings;
  - retrieval result IDs/scores before evidence loading;
  - optionally reranker results only if evaluation proves value.
- Include in every cache key: Knowledge Base ID, normalized filters, query hash,
  embedding model/version, chunking version, retrieval configuration version,
  and a Knowledge Base index generation.
- Increment the index generation when a Document becomes Ready, is deleted, or is
  re-indexed. Use TTLs, entry-size limits, and metrics. Never cache final
  Conversation answers by default.
- Make cache read/write failures degrade to the uncached retrieval path.

### Exit gate

- The evaluation report separates retrieval quality, Citation correctness,
  claim faithfulness, refusal correctness, latency, tokens, and estimated cost.
- Cold and warm runs are reproducible and show cache behavior explicitly.
- Document deletion/re-indexing invalidates cached retrieval immediately through
  generation changes.
- Redis cache failure does not break a Grounded Answer and cannot cause
  cross-Knowledge-Base results.

### Portfolio proof

Publish one quality/latency/cost report and one trace-style waterfall for a
representative turn. Explain one tradeoff selected from evidence—for example,
reranker quality lift versus added p95 latency.

## Checkpoint 5 — CI/CD and production observability

### Outcome

Every pull request is automatically checked; tagged releases publish reproducible
containers; and an operator can follow one Document and one question across API,
worker, database, cache, retriever, reranker, and model calls.

### Work

- Add GitHub Actions pull-request CI:
  - backend Ruff and pytest;
  - frontend ESLint, Vitest, and production build;
  - PostgreSQL/pgvector/BM25-extension plus Redis integration tests;
  - Alembic upgrade from a clean database and the previous release schema;
  - backend and frontend image builds;
  - a Compose smoke test with stubbed model providers;
  - a fast evaluation subset and report-schema validation.
- Add scheduled/manual full evaluation and capacity workflows. Provider-backed
  jobs use protected secrets and are not required for untrusted fork PRs.
- Add tag-based delivery:
  - immutable backend/frontend image tags and git SHA labels;
  - publish to GHCR;
  - generate an SBOM and release checksums/provenance;
  - attach migrations, release notes, deployment/rollback instructions, and the
    evaluation report used to approve the release.
- Instrument with OpenTelemetry spans and metrics around FastAPI, Celery,
  SQLAlchemy/PostgreSQL, Redis, HTTP model calls, Ingestion stages, and retrieval
  stages. Use existing structured observations as event data during migration.
- Propagate request/Conversation/Document/job correlation IDs across API and
  Celery boundaries.
- Add a Compose `observability` profile with an OpenTelemetry Collector,
  Prometheus-compatible metrics store, and Grafana dashboard, while keeping the
  normal Compose stack smaller.
- Define dashboards and initial SLOs for:
  - Ingestion queue age, success rate, duration, and retries;
  - retrieval and generation p50/p95/p99;
  - insufficient-evidence and validation-failure rates;
  - cache hit rate and invalidations;
  - provider failures and token/cost totals.
- Add log/telemetry redaction tests and a failure drill for PostgreSQL, Redis,
  embedding provider, reranker, and generator unavailability.

Reference: [OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/).

### Exit gate

- A pull request cannot merge with failing tests, lint, migration, image build, or
  smoke evaluation.
- A version tag produces immutable images and a release with rollback guidance.
- A reviewer can open one dashboard/trace and follow a question through every RAG
  stage without seeing private content.
- Core chat still works when the optional observability profile is not running.

### Portfolio proof

Include a CI badge, release badge, architecture diagram, and redacted trace/dashboard
screenshot in the README.

## Checkpoint 6 — Portfolio release

### Outcome

A recruiter can understand the problem, architecture, engineering decisions,
measured results, and demo path in a few minutes.

### Work

- Rewrite the README around outcomes:
  1. what problem mikuRAG solves;
  2. a small architecture diagram;
  3. a 5-minute Docker demo;
  4. supported formats and Citation behavior;
  5. retrieval/evaluation results;
  6. reliability, security, and privacy boundaries;
  7. tradeoffs and known limitations.
- Commit representative, reproducible evaluation and benchmark artifacts with the
  exact commands/configurations that produced them.
- Add ADRs for BM25 storage, chunk experiment identity, reranker fallback, cache
  invalidation, evaluation judge, and telemetry privacy.
- Add screenshots or a short demo video covering background Ingestion, metadata
  filters, a code Citation, a PDF Citation, evaluation comparison, and a trace.
- Publish a versioned `v1.0` portfolio release only after Checkpoints 0–5 pass.
- Prepare truthful résumé bullets using measured values, for example:
  - “Built a self-hosted FastAPI RAG system with asynchronous multi-format
    Ingestion, PostgreSQL/pgvector, BM25 + dense retrieval, RRF, reranking, and
    source-level Citations.”
  - “Created a versioned RAG evaluation harness measuring Recall@K, MRR, NDCG,
    claim faithfulness, p95 latency, tokens, and estimated cost across chunking
    and retrieval ablations.”
  - Add numeric improvements only after the committed report exists.

### Exit gate

- A new reviewer can reproduce the smoke demo and small evaluation from a clean
  clone using documented commands.
- Every headline README/resume number links to a committed report and configuration.
- Known limitations—including external embedding privacy and optional OCR/model
  requirements—are stated plainly.

## Optional Checkpoint 7 — Neo4j / GraphRAG

### Start condition

Begin only after the core portfolio release is complete and the evaluation set
contains enough multi-hop/entity-relationship questions to judge a graph path.

### Outcome

An optional graph retriever improves a measured class of relationship/multi-hop
questions without becoming a second authority or weakening deletion and access
control.

### Work

- Add Neo4j under a Compose profile and feature flag.
- Extract versioned entities/relations asynchronously from Ready Documents.
- Store Document/chunk IDs and source locators on every graph fact; do not store
  graph-only claims that cannot be traced to evidence.
- Use an outbox/rebuild process so PostgreSQL state drives graph updates and
  deletion/revocation takes effect safely.
- Add a `GraphRetriever` that returns ranked source chunk IDs. Fuse it as a third
  retrieval list, then reuse the existing reranker, evidence assembly, grounding,
  and Citation pipeline.
- Add graph-specific evaluation cases and compare hybrid vs hybrid+graph on
  multi-hop quality, latency, tokens, and failure modes.

### Exit gate

- The graph can be deleted and rebuilt from PostgreSQL/Documents.
- Disabled Neo4j leaves all core behavior working.
- GraphRAG is promoted from “experimental” only if it improves the frozen
  multi-hop test slice inside the latency/token budget.

## Optional Checkpoint 8 — Bounded agentic retrieval/query decomposition

### Start condition

Begin only after ordinary retrieval failure categories are measured. Do not use
an agent to hide weak chunking or retrieval.

### Outcome

A bounded planner decomposes genuinely multi-part questions, retrieves evidence
for each sub-question, and produces one fully cited answer with predictable
latency and cost.

### Work

- Add a replaceable `QueryDecomposer` returning a typed plan, not arbitrary tool
  calls.
- Enforce maximum sub-queries, retrieval rounds, wall-clock time, tokens, and
  estimated cost.
- Execute independent retrieval calls concurrently where safe, then deduplicate
  and assemble evidence under the existing budget.
- Require every final claim to cite retrieved evidence from the current turn.
- Record the plan, step timings, stop reason, evidence lineage, and budget use
  without logging private text.
- Add adversarial tests for loops, repeated sub-queries, prompt injection in
  Documents, unsupported synthesis, partial provider failure, and cancellation.
- Evaluate simple retrieval vs decomposition only on the multi-part slice; route
  simple questions directly.

### Exit gate

- Simple questions do not pay agentic latency/cost.
- Multi-part completeness improves on the frozen test slice.
- The planner always terminates within configured budgets and falls back safely.
- Disabling the feature returns to the stable non-agentic retriever.

## Capability-to-checkpoint map

| Requested capability | Checkpoint |
| --- | --- |
| PDF/HTML/Markdown/code Ingestion | 1 |
| Async/background indexing | Existing; consolidate and prove in 0–1 |
| Chunking experiments | 2 |
| BM25 + vector hybrid retrieval | 3 |
| Reciprocal-rank fusion | Existing; measure and tune in 2–3 |
| Reranker | Existing interface; real provider and evaluation in 3 |
| Metadata filtering | Metadata in 1; retrieval/API/UI filters in 3 |
| Citations/source tracing | Existing; extend locators in 1 and evaluate in 2/4 |
| Query rewriting | Existing; typed plan and ablation in 3 |
| Retrieval evaluation dataset | 2 |
| Recall@K / MRR / NDCG | 2 |
| Groundedness / faithfulness evaluation | 4 |
| Latency and token/cost measurements | 2 baseline; complete in 4 |
| PostgreSQL + pgvector | Existing and retained throughout |
| Redis caching | 4 |
| FastAPI backend | Existing and retained throughout |
| Docker | Existing; smoke/release hardening in 0 and 5 |
| CI/CD | 5 |
| Observability | Existing logs; full metrics/traces in 5 |
| Neo4j/GraphRAG | Optional 7 |
| Agentic retrieval/query decomposition | Optional 8 |

## Core release definition

mikuRAG is ready to present as a strong portfolio project at the end of
Checkpoint 6. Neo4j and agentic retrieval are bonuses, not requirements.

The core release is complete only when:

- a clean clone has a documented, repeatable Docker demo;
- all supported source types produce correct source-specific Citations;
- background Ingestion and re-indexing are durable and observable;
- the project publishes real vector/BM25/hybrid/reranker ablations;
- Recall@K, MRR, NDCG, Citation correctness, faithfulness, latency, tokens, and
  estimated cost come from an executable versioned dataset;
- metadata filters and authorization are enforced before ranking;
- Redis caching is bounded, versioned, privacy-safe, and optional;
- CI verifies code, migrations, containers, smoke behavior, and evaluation schema;
- tagged releases publish reproducible images and rollback instructions;
- every public performance claim links to evidence in the repository.

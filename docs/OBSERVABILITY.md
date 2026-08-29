# Observability and CI/CD (Checkpoint 5)

mikuRAG ships two things in this checkpoint:

1. **Pull-request CI** that every change must pass, plus tag-based releases
   with reproducible images and rollback guidance.
2. **Opt-in OpenTelemetry observability**: correlation IDs, traces, and
   metrics that let an operator follow one question across API, worker,
   database, cache, retriever, and model calls — without exposing private
   content.

Everything here is optional. With `MIKURAG_OTEL_ENABLED=false` (the default)
and the observability Compose profile off, the core chat flow is unchanged.

## Correlation IDs

Every HTTP request gets a correlation identifier:

- an inbound `X-Request-ID` header is adopted when it is safe (alphanumeric
  plus `._:/-`, at most 128 characters); anything else is replaced with a
  fresh generated ID;
- the ID is echoed on the response as `X-Request-ID`;
- it is attached to every log record, to `mikurag_observation` JSON events
  (`correlation_id` field), and to the OpenTelemetry server span
  (`mikurag.request_id`);
- tasks enqueued during the request stamp the ID into Celery message headers
  (`mikurag-correlation-id`); workers adopt it in `task_prerun`, so worker
  logs, observations, and task spans carry the initiating request's ID.

To follow one question end to end: take the `X-Request-ID` from the response,
then find the same ID on the Celery task spans and the
`rag_turn_measurement` observation line.

## OpenTelemetry instrumentation

`app/telemetry.py` installs tracing and metrics when `MIKURAG_OTEL_ENABLED=true`
and the optional `otel` dependency extra is present (it is included in the
backend image and the `dev` extra). Every failure degrades gracefully:

- the flag off → no-op;
- packages missing → warning + no-op;
- a setup failure → warning, providers disabled, application unaffected;
- an unreachable collector → exporters fail in the background, requests and
  tasks are never blocked (covered by a drill test);
- metric recording errors are swallowed by design.

Instrumentation covers FastAPI (server spans + HTTP metrics), SQLAlchemy
(parameterized statements only — bind values are not captured), Redis,
Celery (produce/consume task spans), and outgoing httpx (embedding and
generation provider calls). Application stages additionally emit explicit
spans: `rag.rewrite`, `rag.embed`, `rag.retrieve`, `rag.summary`,
`rag.generate`, `rag.persist`, plus per-document attributes on ingestion task
spans.

### Privacy rules

Span attributes and metric dimensions carry identifiers, versions, counts,
durations, and statuses only. Query text, answers, Document text, excerpts,
and API keys never appear in telemetry. Concretely:

- HTTP instrumentation captures URLs and status codes, never bodies;
- request/response headers are not captured (no
  `OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_*` is set);
- turn measurements are mirrored into metrics through an allowlist of
  dimensions (`stage`, `outcome`, `failure_category`, `kind`, `cache`,
  `result`) — enforced by tests;
- the existing structured `mikurag_observation` log lines keep their redaction
  guarantees and gain only the `correlation_id` field.

The structured observation log remains the primary event record during the
migration; spans/metrics complement it rather than replace it.

## Metrics

Application metrics (exported via the collector's Prometheus exporter; dots
become underscores, monotonic counters gain `_total`, durations are recorded
in seconds so histograms gain `_seconds`):

| OTel instrument | Prometheus name | Labels | Meaning |
| --- | --- | --- | --- |
| `mikurag.rag.stage.duration` (histogram, s) | `mikurag_rag_stage_duration_seconds_*` | `stage`, `service_name` | rewrite/embed/vector/bm25/fusion/rerank/expansion/generation/validation/persistence latency |
| `mikurag.rag.turn.duration` (histogram, s) | `mikurag_rag_turn_duration_seconds_*` | `outcome`, `failure_category` | end-to-end turn latency |
| `mikurag.rag.turns` (counter) | `mikurag_rag_turns_total` | `outcome`, `failure_category` | turn outcomes |
| `mikurag.rag.tokens` (histogram) | `mikurag_rag_tokens_*` | `kind` | prompt/completion/query_embedding/evidence tokens |
| `mikurag.cache.operations` (counter) | `mikurag_cache_operations_total` | `cache`, `result` | derived-cache hit/miss/error |
| `mikurag.ingestion.documents` (counter) | `mikurag_ingestion_documents_total` | `outcome` | ingestion outcomes |
| `mikurag.ingestion.duration` (histogram, s) | `mikurag_ingestion_duration_seconds_*` | `outcome` | ingestion duration |
| `mikurag.ingestion.embedding_inputs` (counter) | `mikurag_ingestion_embedding_inputs_total` | — | embedding inputs submitted |

`service_name` distinguishes `mikurag-backend` from `mikurag-worker`.
HTTP API latency comes from the FastAPI instrumentation (stable semantic
convention `http_server_request_duration_seconds_*`; both old and new
conventions are emitted via `OTEL_SEMCONV_STABILITY_OPT_IN=http/dup`).

## Dashboards and initial SLOs

Run:

```sh
docker compose -f compose.yaml -f compose.observability.yaml --profile observability up -d
```

This starts:

- **OpenTelemetry Collector** (OTLP :4317/:4318) → metrics scraped by
  Prometheus, traces forwarded to Tempo; collector self-telemetry
  (`otelcol_*`) is exposed on :8888 and scraped under the
  `mikurag-otel-collector-self` job;
- **Prometheus** (:9090) with the initial SLO-style alert rules from
  `observability/prometheus/rules.yml`;
- **Tempo** (trace store, 72 h retention);
- **Grafana** (:3000) with the provisioned "mikuRAG / Quality and Operations"
  dashboard (`observability/grafana/dashboards/mikurag.json`).

The dashboard panels cover: per-stage RAG latency (p50/p95), turn outcomes,
end-to-end turn latency, API latency, token usage by kind, cache operations,
ingestion outcomes/duration, embedding volume, and failure categories.

Initial SLOs (alerts are examples, not tuned production thresholds):

| Signal | Initial SLO | Rationale |
| --- | --- | --- |
| Retrieval p95 | < 1500 ms | the acceptance gate (`MIKURAG_ACCEPTANCE_RETRIEVAL_P95_TARGET_MS`) |
| Turn failure rate | < 5% over 30 min | provider/grounding failures should be rare |
| Ingestion failure rate | < 5% over 30 min | parser/provider errors must not accumulate |
| Cache error rate | ~0 (fail-open) | Redis is not on the correctness path, but sustained errors mean it is unhealthy |

## CI/CD

`.github/workflows/ci.yml` runs on every pull request and push to `main`:

| Job | What it proves |
| --- | --- |
| Backend / Ruff + pytest | lint and the unit suite (integration tests excluded) |
| Frontend / ESLint + Vitest + build | lint, tests, production build |
| Integrations | Alembic upgrade from a clean database and from the previous release schema (`0005` → head, matching `scripts/migration_smoke.py`), plus real-PostgreSQL/pgvector/Redis integration tests (`pytest -m integration`) |
| Images | backend and frontend container builds (with GHA layer cache) |
| Compose smoke | `scripts/compose_smoke.py` under the isolated `mikurag-smoke` project: stubbed model providers, migration, demo seed, resumable upload, a grounded cited answer, a two-case evaluation subset with report-schema validation, and (with `MIKURAG_SMOKE_OTEL=1`) verification that Prometheus scrapes the collector (exporter :8889 and self-telemetry :8888), that a real mikuRAG turn metric reached Prometheus, and that the collector accepted spans |

`.github/workflows/release.yml` runs on `v*` tags: builds immutable
backend/frontend images tagged with the git tag and full SHA (with SBOM and
provenance attestations), publishes them to GHCR, and creates a GitHub
release with SBOMs, SHA-256 checksums, deployment steps, and rollback
guidance.

`.github/workflows/evaluation.yml` is the scheduled/manual full
provider-backed evaluation. It uses protected secrets, is gated to the origin
repository, and is never required for pull requests.

### Local commands

```sh
# unit checks (as CI runs them)
(cd backend && python -m ruff check --no-cache . && python -m pytest)
(cd frontend && npm run lint && npm test && npm run build)

# integration tests against a live database/Redis
(cd backend && python -m pytest -m integration)

# compose smoke with the deterministic provider stub
python scripts/compose_smoke.py

# compose smoke including the observability pipeline verification
MIKURAG_SMOKE_OTEL=1 python scripts/compose_smoke.py
```

## Provider stub for tests

`scripts/provider_stub.py` (stdlib-only, runs in a bare `python:3.12-slim`
container under the `smoke` Compose profile) implements both provider
contracts deterministically:

- embeddings: 768-dimensional vectors hashed from character trigrams, so
  lexically related text really ranks higher (shared substrings map to shared
  dimensions);
- chat completions (streaming and non-streaming): query rewrites echo the
  current question; grounded answers cite exactly the evidence IDs the server
  supplied in the prompt.

The `test` environment is the only one that accepts plain-HTTP provider
endpoints, so the stub cannot be configured in development or production.

## Failure drills

| Dependency unavailable | Guaranteed behavior | Verified by |
| --- | --- | --- |
| PostgreSQL | readiness reports `error` (503); no crash | `tests/test_health.py` |
| Redis | caches and rate limiting fail open; answers unaffected | `tests/test_rag_cache.py`, integration fail-open test |
| Embedding provider | ingestion marks the Document failed with a safe error; turns fail safely | `tests/test_failure_drills.py`, ingestion tests |
| Generator provider | typed `GenerationProviderError`, safe message, validation retries | `tests/test_rag_service.py`, `tests/test_failure_drills.py` |
| Reranker / pg_search | falls back to fused order / FTS baseline | `tests/test_retrieval_modes.py` |
| Telemetry collector | exporters fail in the background; requests and tasks unaffected | `tests/test_telemetry.py` (drill) |

## Architecture decision

See `docs/adr/0007-optional-opentelemetry-telemetry.md` for why observability
is flag-gated, why the structured observation log is retained, and how the
privacy boundary is enforced.

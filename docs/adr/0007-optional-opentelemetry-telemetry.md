# ADR-0007: Optional, flag-gated OpenTelemetry telemetry

- Status: Accepted (2026-08-28)
- Context: Checkpoint 5 of the roadmap (CI/CD and production observability)

## Context

mikuRAG already emits structured `mikurag_observation` JSON log lines and
persists redacted per-turn measurements. Checkpoint 5 asks for OpenTelemetry
spans and metrics across FastAPI, Celery, SQLAlchemy, Redis, and model calls,
plus correlation IDs that follow one request across API and worker
boundaries.

The delivery rules constrain the design:

1. A failed optional service must not corrupt Ingestion or prevent the stable
   retrieval path from operating (rule 4).
2. No Document text, query text, evidence text, credentials, or personal data
   may be emitted in telemetry (rule 7).
3. The normal Compose stack must stay small (checkpoint 5 outcome).

## Decision

1. **Observability is a feature flag, off by default.** `MIKURAG_OTEL_ENABLED`
   (default `false`) gates all tracing and metrics. Nothing changes for users
   who never enable it, and the core chat exit gate ("core chat still works
   when the optional observability profile is not running") holds trivially.
2. **The `otel` extra is optional at import time.** `app/telemetry.py` guards
   every OpenTelemetry import; without the packages the module is a no-op.
   The backend image ships the extra so production can enable telemetry
   without a rebuild, and the `dev` extra includes it so tests exercise the
   real code paths.
3. **Every telemetry failure degrades to the uncached/unobserved path.**
   Setup failures disable telemetry with a warning; metric recording and
   span-attribute errors are swallowed; an unreachable collector only affects
   background export batches. A drill test proves a closed collector port
   never breaks a request.
4. **Correlation is independent of OpenTelemetry.** The `X-Request-ID`
   middleware, the log record factory, and Celery header propagation work
   with telemetry disabled, because log-line correlation is useful on its own
   and must not depend on an optional dependency.
5. **Redaction is enforced by construction and by tests.** Metric dimensions
   come from an allowlist (`stage`, `outcome`, `failure_category`, `kind`,
   `cache`, `result`); span attributes are set explicitly at a handful of
   call sites with identifiers/counts only; request/response bodies and
   headers are never captured; SQLAlchemy statements are parameterized, and
   bind values are not recorded. Tests assert that a private field injected
   into a measurement cannot become a metric dimension.
6. **The structured observation log is retained.** Log lines remain the
   durable, greppable event record (and the source the evaluation stories
   were built on); OTel spans/metrics complement them. Correlation IDs are
   added to observation payloads rather than moving observations into the
   OTel log bridge.
7. **The observability stack is a Compose profile.** Collector, Prometheus,
   Tempo, and Grafana live under `profiles: ["observability"]` with pinned
   images and provisioned dashboards, keeping the default stack small.

## Consequences

- Enabling observability is one env var plus one Compose profile; disabling
  it is equally trivial and always safe.
- The Prometheus metric names follow the OpenTelemetry-to-Prometheus
  compatibility mapping (documented in `docs/OBSERVABILITY.md`); durations
  are recorded in seconds specifically so histogram names get the stable
  `_seconds` suffix instead of depending on unit conversion of `ms`.
- The CI compose smoke can verify the whole pipeline (backend → collector →
  Prometheus/Tempo) with the deterministic provider stub, so observability
  has its own regression gate like every other feature.

# Checkpoint 0–5 Review — Problem Report

Reviewed 2026-08-29 against the working tree (local `main` at `1dfc03e`, plan.md
checkpoint map, and the implementation behind each checkpoint's exit gate).

## Summary

Checkpoints 0–3 are substantially implemented and evidenced with real code and
real measured numbers; Checkpoint 4 and 5's "remaining work" docs are accurate
rather than optimistic. The problems below fall into four groups:

1. Nothing since the `v0.1.0` tag is committed or pushed — Checkpoint 5's exit
   gate cannot start and all Checkpoint 4/5 work is at risk.
2. One deliverable is missing entirely: the Checkpoint 2 chunking-profile
   comparison.
3. The CI workflows contain concrete bugs that will fail or misbehave on the
   first run, including one privacy-relevant default.
4. Checkpoint 4's exit-gate items are all still open, and a few test-coverage
   gaps sit under load-bearing behavior.

---

## 1. Cross-cutting: everything is uncommitted and unpushed

- `origin/main` is frozen at `dda8dab` (the `v0.1.0` checkpoint-0 tag). Local
  `main` is 17 commits ahead, and ~55 files are modified or untracked.
- Every Checkpoint 4/5 deliverable is uncommitted: `.github/`,
  `backend/app/evaluation/faithfulness.py`, `backend/app/rag/cache.py`,
  `backend/app/rag/turn_measurement.py`, `backend/app/correlation.py`,
  `backend/app/telemetry.py`, migration `0011`, `backend/tests/integration/`,
  `observability/`, `compose.observability.yaml`, and all new tests.
- Consequences:
  - Checkpoint 5's exit gate ("push the branch and confirm the workflow runs
    green") cannot begin; none of the four workflows has ever executed.
  - The portfolio story is invisible on GitHub.
  - A disk failure loses Checkpoints 4 and 5 wholesale.
- **Action:** commit the tree in reviewable slices and push before anything
  else.

## 2. Checkpoint 0 — consolidated baseline

Verified: re-index lifecycle (queued/running/paused/completed/failed, bounded
retries, idempotency in `backend/app/ingestion/reindexing.py`), the
deterministic 9-question demo seed across five source types
(`backend/app/demo_data/v1/`), paired `scripts/mikurag.sh` / `mikurag.ps1`,
honest README status language, atomic Ready transitions, and upload
checkpointing.

Problems:

- **Restart-safety proof lives outside the test suite.** The
  stale-PROCESSING reclaim path (`backend/app/ingestion/tasks.py:91-126`) has
  no pytest, and `scripts/restart_smoke.py` is Docker-dependent. The exit gate
  requires proven restart behavior, but CI never exercises it.
- **Dead DB state.** The `reindex_jobs` check constraint allows `'cancelled'`
  (`backend/app/models.py:266-268`) but `ReindexJobStatus` has no such member.
- **Commit hygiene regressed.** Throwaway messages ("first commit", "v0.2",
  "exe runner for eval") undermine the "understandable commits" exit gate.

## 3. Checkpoint 1 — heterogeneous ingestion and provenance

Verified: extractor registry replacing media-type branches
(`backend/app/ingestion/extractors/registry.py`), HTML/Python/TS/JS extractors
with heading paths, DOM selectors, symbols, and line ranges; provenance fields
in model + migration `0008`; per-source citation locators asserted in frontend
tests; admin-visible ingestion stage/progress/attempts.

Problems:

- **Document deletion/purge is untested.** `run_purge`
  (`backend/app/ingestion/tasks.py:509`) has no unit or integration test. This
  matters more than it looks: purge increments the Knowledge Base index
  generation that Checkpoint 4's cache invalidation depends on, so an untested
  deletion path sits underneath the cache-correctness story.
- **The document-level `/retry` endpoint is untested**
  (`backend/app/api/documents.py:56-70`).

## 4. Checkpoint 2 — executable evaluation and chunking lab

Verified: executable runner through the real worker
(`backend/app/evaluation/runner.py`), 64 reviewed gold cases (up from 8),
train/dev/test splits (35/16/13), graded-qrels NDCG, bootstrap CIs, three
versioned chunker profiles with persisted config hashes, JSON+Markdown report
generation.

Problems:

- **The documented exit-gate deliverable does not exist.** No committed
  comparison of `legacy_char_v1` vs `token_recursive_v1` vs `hierarchical_v1`:
  no `backend/evaluation/results/compare/` directory, and no `docs/CHUNKING-*.md`
  contains measured profile numbers (those docs are methodology only).
  `docs/EVALUATION-RUNNER.md` promises this artifact. The comparison tooling
  (`evaluation_cli.py compare`) exists and is tested; the experiment was never
  run or never written up. This is the largest gap between plan and reality in
  Checkpoints 0–3.
- **Headline numbers rest on a 13-case test split.** One case moves Recall@10
  by 0.077; the vector-vs-BM25 bootstrap CIs overlap. The CIs were computed but
  omitted from the published table in `docs/EVALUATION-CHECKPOINT-3.md`.
- **Governance inconsistency.** `gold_v1` declares `headline_eligible: false`,
  yet the ablation doc marks its rows "valid for headline: yes". No code
  consumes the flag, so nothing enforces it.
- **The FTS baseline is near-useless on this corpus** (Recall@10 = 1/13), which
  flatters BM25's advantage and means the "portable fallback" leg contributes
  nothing to hybrid quality.

## 5. Checkpoint 3 — BM25, hybrid retrieval, filters, rewriting, reranking

Verified: true pg_search BM25 (migration `0010`, pinned ParadeDB image,
ADR-0005 with the CTID-staleness finding, FTS fallback, post-delete repair),
all five experiment modes, filters pushed into every SQL leg before `.limit()`,
cross-KB leakage tests parametrized over all five modes, cross-encoder
reranker with timeout/fallback, typed query plan with identifier preservation.
All defaults remain feature-off. `docs/EVALUATION-CHECKPOINT-3.md` has real
committed numbers with run IDs.

Problems:

- **The rewrite leg proved nothing.** All 3 eligible follow-up rewrites hit
  provider timeouts, so original-vs-rewritten is measured but uninformative,
  and rewriting stays off by default. The plan's ablation requirement for
  rewriting is unmet in practice.
- **The reranker exceeds the latency budget.** Hybrid+reranker p95 is 4914 ms
  against the plan's own 1,500 ms retrieval-p95 budget. Consistent with keeping
  it off, but no configuration currently satisfies the
  reranker-within-budget gate — that plan item is blocked, not just deferred.
- **Filter-pushdown unit test omits an explicit `bm25` mode case**
  (`backend/tests/test_retrieval_modes.py`, parametrizes vector/fts/hybrid
  only; same code path, low risk).

## 6. Checkpoint 4 — faithfulness, cost accounting, caching (in progress)

Verified: deterministic faithfulness evaluator v1.0.0 integrated into
runner/reporting (`backend/app/evaluation/faithfulness.py`), full per-turn
stage/token/cost measurement with redaction
(`backend/app/rag/turn_measurement.py`), HMAC-keyed caches with TTL/size bounds
and fail-open (`backend/app/rag/cache.py`), migration `0011` generation bumps
at Ready/delete/purge.

Problems (all confirmed still open, matching
`docs/EVALUATION-CHECKPOINT-4.md`):

- **None of the four exit-gate integration tests exist:** cold/warm
  equivalence, immediate deletion/re-index invalidation via generation bump,
  cache-level cross-KB isolation, or Grounded-Answer-with-Redis-down.
  `backend/tests/integration/` covers retrieval primitives and cache
  roundtrips only.
- **`backend/app/rag/pricing_v1.json` contains no external prices**, so cost
  estimates are structurally complete but never `estimate_complete`.
- **No cold/warm report or trace-style waterfall exists** anywhere
  (`backend/evaluation/results/` holds only Checkpoint-3 runs).
- **The human claim-map audit has not started**;
  `human_audit_required` is hardwired `False` (`faithfulness.py:31`).
- **Test coverage is thin where it matters:** 3 faithfulness tests, 1
  turn-measurement test, 3 cache tests; the p50/p95/p99 report block in
  `backend/app/evaluation/reporting.py` has zero test references.

## 7. Checkpoint 5 — CI/CD and observability (implemented locally, CI never run)

Verified: structurally complete workflows (backend/frontend checks, paradedb +
Redis integration services, image builds, compose smoke with stub providers, a
real Prometheus probe for the observability pipeline, GHCR + SBOM/provenance
release); correlation IDs, flag-gated OTel, dashboards, SLO rules, and
redaction tests all present.

Concrete workflow defects:

1. **Wrong "previous release schema" revision.** `ci.yml:109` upgrades from
   revision `0005`, but `v0.1.0` shipped migrations `0001–0007`. The actual
   release upgrade path (`0007 → 0011`) — the one a real user will run — is
   never tested. Fix to `0007` (the clean-DB downgrade at `ci.yml:101` is also
   off by two).
2. **Privacy-relevant default in `evaluation.yml`.** It sets
   `MIKURAG_EMBEDDING_API_KEY` but not the endpoint/model, so evaluation
   silently uses the config default — the public DashScope endpoint
   (`backend/app/config.py:59-62`). Private evaluation corpus text would be
   embedded by a public cloud provider unless overridden. This brushes against
   the plan's own delivery rule 7 and ADR-0007's privacy posture.
3. **The release flow silently no-ops outside the origin repo.** The
   `github.repository == 'Manticore0918/mikuRAG'` guard in `evaluation.yml:37`
   skips the gated evaluation job, and a skipped `needs:` cascades to skip
   image publish and the GitHub release — a tag pushed after a repo rename
   produces nothing, with only skip statuses.
4. **No `timeout-minutes` on any job** in any of the four workflows — a hung
   service check blocks the runner for 6 hours.
5. **The `paradedb/paradedb:0.25.5-pg16` tag is unverified** against Docker
   Hub (no local network access) — if wrong, the integration job and compose
   smoke fail immediately on first run.
6. **The smoke's "report-schema validation" is a 4-key presence check**
   (`scripts/compose_smoke.py:457-459`), not schema validation — weaker than
   the exit-gate wording.
7. **Manual prerequisites are untracked:** the `evaluation` GitHub environment,
   its secrets, and branch-protection required checks must all be created
   before `release.yml` can pass.

---

## Recommended priority order

1. Commit the working tree in coherent slices and push; fix the `0005 → 0007`
   CI bug and add `timeout-minutes` before the first CI run so it isn't wasted.
2. Fix the `evaluation.yml` embedding endpoint default (or pin it explicitly to
   the intended provider).
3. Run and commit the Checkpoint 2 chunking comparison — the one missing
   deliverable for an otherwise-complete checkpoint; the tooling already
   exists (`python -m app.evaluation_cli compare`).
4. Build the four Checkpoint 4 integration tests (also the natural place to
   cover the currently-untested purge path from Checkpoint 1).
5. Add confidence intervals to the published ablation table and either honor or
   remove the `headline_eligible` flag.

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

---

# Addendum — First CI run failed (2026-08-29, run 33246605033)

The `v0.1` commit was pushed and the CI workflow ran for the first time. Three
of five jobs failed; the frontend job and the image-builds job passed. All
three failures trace to two root causes in the pushed tree, not to workflow
bugs.

## Root cause A — `.gitignore` excluded the `app.uploads` package from the commit

- The `.gitignore` rule `uploads/` (under "Runtime data and local databases",
  meant for the runtime upload-storage directory) is unanchored, so it matches
  any directory named `uploads` at any depth — including the application
  package `backend/app/uploads/`.
- As a result the entire package — `__init__.py`, `storage.py`, `cleanup.py`,
  `tasks.py` — is absent from the pushed repository (verified with
  `git ls-files backend/app/uploads/` → empty, and a disk-vs-tracked sweep that
  found exactly these four untracked source files).
- Everything works locally because the files exist on disk; CI checks out only
  git-tracked files, so it breaks. This also means **anyone cloning the GitHub
  repo gets a backend that cannot boot**: `backend/app/main.py:16,25,80`
  imports the uploads router and `app.uploads.cleanup.reconcile_upload_sessions`
  at startup.

Effects on two jobs:

1. **Integrations job — failed.** Test collection raises
   `ModuleNotFoundError: No module named 'app.uploads'` in
   `test_auth_api.py`, `test_authorization.py`, `test_health.py`,
   `test_upload_api.py`, and `test_upload_storage.py`.
2. **Compose smoke job — failed.** The CI-built backend image lacks the
   package, the backend crashes on import, its healthcheck never passes, and
   compose aborts with "dependency failed to start: container
   mikurag-smoke-backend-1 is unhealthy".

Fix:

- Anchor the ignore rule so it cannot match the package: replace `uploads/`
  with `/uploads/` and add `/backend/uploads/` (the runtime directory is
  `./uploads` relative to the backend working directory, `config.py:22`).
- `git add backend/app/uploads/` so the package is committed.

## Root cause B — `.pytest_tmp` debris was committed (naming mismatch)

- The `.gitignore` has `.pytest-tmp/` (hyphen) and Ruff's `extend-exclude` in
  `backend/pyproject.toml:66` lists `.pytest-tmp` — but the actual pytest
  `--basetemp` directory is `backend/.pytest_tmp` (underscore). Neither pattern
  matches, so ~150 test-run artifacts (including deliberately malformed files
  such as `broken.py` and a fixture `worker.py` with an unused `asyncio`
  import) were committed and pushed.
- **Ruff job — failed** with 6 errors, 4 of them inside
  `backend/.pytest_tmp/` (I001 unsorted imports, F401 unused import). The other
  2 are genuine violations independent of the debris.

Fix:

- `git rm -r --cached backend/.pytest_tmp` (and delete the directory), add
  `.pytest_tmp/` to `.gitignore`, and align the Ruff `extend-exclude` to the
  real name.

## Standalone issue found by the Ruff job

Two genuine `I001` (import block un-sorted/un-formatted) violations in
`backend/tests/test_upload_api.py:1` and
`backend/tests/test_upload_storage.py:1` — the `import pytest` line needs to be
separated from the `from app.uploads.storage import ...` block. These will
still fail Ruff after the debris is removed and must be fixed regardless.

## Results table

| Job | Result | Cause |
| --- | --- | --- |
| Backend / Ruff + pytest | failed | `.pytest_tmp` debris (4 errors) + 2 real I001s |
| Integrations / PostgreSQL + Redis | failed | `app.uploads` package missing from commit |
| Compose smoke | failed | same missing package → backend container unhealthy |
| Frontend / ESLint + Vitest + build | passed | — |
| Images / backend + frontend build | passed | — |

Note: image builds "pass" despite the missing package because a build only
copies files; nothing imports the app. CI passing on this job proves nothing
about bootability — the compose smoke is the check that catches it, which is
exactly what happened.

## Fix sequence

1. Anchor `uploads/` in `.gitignore` to `/uploads/` + `/backend/uploads/`; add
   `.pytest_tmp/`.
2. `git add backend/app/uploads/` and `git rm -r --cached backend/.pytest_tmp`.
3. Fix the two I001 import blocks (or run `ruff check --fix`).
4. Align `backend/pyproject.toml:66` Ruff `extend-exclude` with the real
   `.pytest_tmp` name.
5. Commit and push; CI should then exercise the paths the review predicted
   (previous-release migration `0007 → 0011`, ParadeDB image tag, timeouts).
6. Also revisit why the squashed `v0.1` commit was never validated locally
   against a clean checkout (`git clone` or `git stash -u` style check) — a
   clean-checkout smoke before pushing would have caught both root causes.

## Resolution (2026-08-29)

All three failed jobs are fixed and CI is green (runs 33247548833 →
33247933591, commits `9b7022c` and `0206601`):

- Commit `9b7022c` anchored the `uploads/` ignore rules, committed the
  `backend/app/uploads/` package, removed the 133 committed `.pytest_tmp`
  debris files, and aligned the Ruff exclude with the real name. This fixed
  the Ruff job and the `app.uploads` import failures. (The two I001 violations
  were a symptom, not a cause: without the `app.uploads` submodule on disk,
  Ruff could not classify `app.uploads.storage` as first-party.)
- Commit `0206601` fixed two deeper bugs that the first green Ruff step
  exposed, both in code that had never run against a real environment:
  - The integration fixture inserted a 32-character `content_hash`, violating
    the `chunks_content_hash_length_ck` (64) database check; it now inserts a
    sha256 hex digest.
  - The compose smoke's `evaluate` step bind-mounted
    `./backend/evaluation/results` into the unprivileged container; on a Linux
    runner the mount is not writable by the image's uid, so creating the run
    directory failed. The smoke script now creates the tree host-side and
    opens its permissions before the run.

Final job status on run 33247933591: Compose smoke, Integrations, Frontend,
Backend Ruff+pytest, and Images — all SUCCESS.

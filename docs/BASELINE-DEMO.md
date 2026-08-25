# Checkpoint-0 baseline demo

This demo is a small, redistributable proof path for the first portfolio
baseline. Its inputs are versioned under `backend/app/demo_data/v1/`:

- `operations-handbook.pdf`: two pages with exact identifiers, operational
  rules, privacy constraints, and Citation-friendly page locators.
- `release-guide.md`: headings and prose for paraphrase and follow-up questions.
- `questions.json`: the six scenario IDs, turns, expected facts, source names,
  locators, refusal expectation, and authorization expectation.

`scripts/build_demo_pdf.py` regenerates the PDF deterministically. Do not change
the PDF or Markdown without incrementing the dataset version and updating the
manifest.

## Run the proof

1. Configure `.env`, including a working embedding provider and local generation
   provider.
2. Start and migrate the Installation with the platform entry point:

   ```powershell
   .\scripts\mikurag.ps1 setup
   ```

   ```sh
   sh ./scripts/mikurag.sh setup
   ```

3. Set `MIKURAG_DEMO_ADMIN_PASSWORD` and `MIKURAG_DEMO_USER_PASSWORD` in the
   current shell. They are passed only to the one-off seed container and are not
   written to the repository.
4. Run `seed`. It creates the first Administrator only when none exists, creates
   the non-Administrator `baseline-demo` User, creates public and restricted
   Knowledge Bases, grants access only to the public one, copies both source
   files into managed storage, queues real Celery Ingestion, and waits for both
   Documents to become Ready.
5. Run `smoke`. It constructs both legacy and hierarchical chunks from both
   source files, then verifies Ready Documents, child chunks, exact evidence,
   and the positive and negative access grants.
6. Run `restart-smoke`. It restarts the backend with an open resumable upload,
   resumes at the same confirmed offset, completes it while the worker is down,
   restarts the worker, and waits for the resulting Document to become Ready.
7. Sign in as `baseline-demo`, open `mikuRAG Baseline Demo`, and ask the cases in
   manifest order. Expand every Citation before moving to the next case.

The separate `migrations` workflow creates disposable PostgreSQL databases and
verifies a clean upgrade, an upgrade from the previous `0005` schema, a rollback
from `0007` to `0005`, and a re-upgrade to `head`. It never runs downgrade
against the configured application database.

## Expected interaction

| Scenario | Expected behavior |
| --- | --- |
| Exact identifier | Answer contains `MIKU-4271`; Citation opens PDF page 1. |
| Paraphrase | Answer says the customer update follows passing health checks and occurs within thirty minutes; Citation names `release-guide.md`. |
| Follow-up | First answer gives Tuesday 09:00-11:00 Singapore time; “Who has to approve it?” resolves “it” to the rollout and answers “release manager.” |
| Citation | Answer lists the prohibited telemetry text and Citation opens PDF page 2. |
| Insufficient evidence | The system explicitly says it cannot answer the parking question reliably and emits no invented policy. |
| Authorization | `baseline-demo` cannot list or open `mikuRAG Baseline Restricted`; a direct request returns `404 Not Found`. |

## Restart checks

- Windows: `.\scripts\mikurag.ps1 restart-smoke`
- macOS/Linux: `sh ./scripts/mikurag.sh restart-smoke`
- During a resumable upload, restart the API container and reselect the same file.
  The confirmed offset must remain unchanged and the upload must resume.
- While one seed Document is Processing, restart the worker. Celery late
  acknowledgement plus the stale-Ingestion reclaim path must eventually return
  it to Ready without exposing partial chunks.
- Run the hierarchical smoke tests with the default environment, then with
  `MIKURAG_CHUNKING_VERSION=hierarchical_v1` and
  `MIKURAG_HIERARCHICAL_RETRIEVAL_ENABLED=true`. The default `.env.example`
  remains `legacy` and `false`.

## Portfolio capture

Capture a screenshot or short GIF after all checks pass:

1. show the two Documents transitioning through background Ingestion;
2. ask the exact-identifier question;
3. expand the page-1 Citation;
4. ask the insufficient-evidence question;
5. show the restricted Knowledge Base returning `404` for `baseline-demo`.

The current real-UI proof is stored at
`docs/assets/checkpoint-0-baseline.png` and linked from the README. A GIF may
replace or supplement it later. Do not use a mocked or manually edited capture
as portfolio proof.

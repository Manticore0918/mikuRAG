# Portfolio release readiness

Checkpoint 6 began on 2026-08-30. The repository is a release candidate, not yet
a `v1.0` release.

## Gate status

| Gate | Evidence | Status |
| --- | --- | --- |
| Clean-clone Docker demo | `scripts/compose_smoke.py`, baseline demo guide | Local working-tree run passed; clean-clone release-commit run remains |
| Multi-source Citations | baseline corpus and Citation tests | Implemented |
| Retrieval evaluation | committed diagnostic artifact and exact source revision | Implemented, diagnostic only |
| Answer faithfulness | deterministic evaluator and schema-v3 corpus | Provider-backed report and human calibration still required |
| Cold/warm cache experiment | integration tests and versioned cache identities | Real report still required |
| CI | run `33247933591`: backend, frontend, integration, images, Compose smoke | Green at revision `0206601` |
| Release workflow | tag-triggered images, SBOMs, checksums, evaluation attachment | Implemented but not exercised for `v1.0` |
| Restart durability | `docs/evidence/release-candidate-smoke-2026-08-30.json` | Passed backend restart, hard worker kill, stale claim recovery, and failed-parser retry |
| Observability pipeline | `docs/evidence/release-candidate-smoke-2026-08-30.json` | Collector/Prometheus smoke passed with a real turn metric and accepted spans |
| Portfolio media | real PDF Citation screenshot and generated ablation comparison | Authenticated UI and trace captures remain in `PORTFOLIO-MEDIA.md` |

## Do not tag `v1.0` until

- the provider-backed checkpoint 4 answer/cost and cold/warm reports are
  committed with complete experiment identity;
- the human claim-map audit/calibration is recorded;
- the clean-clone Compose demo and small evaluation pass at the release commit;
- the required redacted media is captured from that commit;
- the branch is clean, pushed, and the full CI workflow is green; and
- the tag-triggered release evaluation has the required protected environment
  and provider secrets.

When those conditions hold, create an annotated `v1.0.0` tag. The release
workflow will gate publication on the provider-backed evaluation, publish
immutable backend/frontend images, attach SBOMs and checksums, and create the
GitHub release with rollback guidance.

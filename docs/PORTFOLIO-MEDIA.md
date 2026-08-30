# Portfolio media capture plan

Portfolio media must be captured from a real seeded Installation. Generated UI
mockups and placeholder traces are not acceptable evidence.

## Current evidence

[`assets/checkpoint-0-baseline.png`](./assets/checkpoint-0-baseline.png) is a real
capture of a Grounded Answer with an expanded PDF page Citation. It contains only
the redistributable demo corpus.

## Required release captures

| Capture | Required view | Status |
| --- | --- | --- |
| Background Ingestion | Administrator Document list moving through durable stages to `Ready` | Stack verified; capture needs an authorized Administrator session |
| Metadata filter | Evaluation/API result showing the selected filter and filtered source Citation | Stack verified; capture needs an authorized demo session |
| Code Citation | Expanded Python or TypeScript Citation with path, symbol, and line range | Stack verified; capture needs an authorized demo session |
| PDF Citation | Grounded Answer with expanded page locator | Available |
| Evaluation comparison | Generated ablation comparison tied to the committed evidence JSON | Available: [`assets/retrieval-ablation-gold-v1-test.svg`](./assets/retrieval-ablation-gold-v1-test.svg) |
| Trace | Grafana/Tempo trace showing the RAG stages and correlation ID | Pipeline verified; capture needs an authorized Grafana session |

The 2026-08-30 local working-tree smoke passed migration, restart recovery,
multi-format retrieval, the isolated Compose path, and the complete telemetry
pipeline. Its machine-readable record is
[`evidence/release-candidate-smoke-2026-08-30.json`](./evidence/release-candidate-smoke-2026-08-30.json).

## Capture procedure

1. Start from a clean clone and follow the README demo.
2. Seed only `backend/app/demo_data/v1`; do not use private Documents.
3. Enable the observability profile only for the trace capture.
4. Use a fresh demo User and random identifiers, then crop browser chrome and
   unrelated desktop content.
5. Redact usernames, session values, request headers, hostnames, provider keys,
   local filesystem paths, and any query/answer/Document text not from the demo
   corpus.
6. Keep correlation IDs only when they identify the synthetic demo trace.
7. Save source captures under `docs/assets/` and record the source revision,
   command, and capture date in this file.

The trace must show identifiers, durations, counts, statuses, and component names
only. If query, answer, evidence, or Document text appears, fix the telemetry
source before redacting the image.

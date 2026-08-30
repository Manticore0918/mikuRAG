# Versioned portfolio evidence

This directory contains small, redistributable evidence artifacts selected from
the ignored local runtime-output directories. Each artifact records the command,
configuration, source revision, and interpretation boundary needed to audit it.

| Artifact | What it proves | Claim boundary |
| --- | --- | --- |
| [`retrieval-ablation-gold-v1-test-2026-08-28.json`](./retrieval-ablation-gold-v1-test-2026-08-28.json) | Five retrieval modes ran over the same frozen 13-case test split | Diagnostic synthetic corpus; not headline-eligible |
| [`capacity-smoke-2026-08-30.json`](./capacity-smoke-2026-08-30.json) | The versioned synthetic capacity benchmark runs and emits its schema | Local smoke timing, not production capacity or database latency |
| [`release-candidate-smoke-2026-08-30.json`](./release-candidate-smoke-2026-08-30.json) | Docker migration, multi-format retrieval, restart recovery, isolated Compose, evaluation-schema, and telemetry pipeline checks passed | Dirty local worktree; not a clean-clone or provider-backed benchmark |

Full per-case runtime artifacts remain under `backend/evaluation/results/` and
are ignored because they are numerous and may be provider/environment specific.
Release workflows upload the provider-backed evaluation bundle separately.

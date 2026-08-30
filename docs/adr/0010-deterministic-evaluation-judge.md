# ADR 0010: Use a deterministic reviewed evaluator before an LLM judge

- Status: Accepted (2026-08-30)
- Context: Checkpoint 4 answer faithfulness

## Context

An LLM-as-judge can score paraphrases, but introduces model drift, provider cost,
prompt sensitivity, and a circular failure mode in which one model validates
another without independently reviewed evidence. The first portfolio release
needs an evaluator that can be inspected and rerun offline.

## Decision

The baseline judge is `deterministic_claim_support` version `1.0.0`. Reviewed
evaluation cases define acceptable answer facts, expected claims, required
passage IDs, refusal expectations, and conflicting-evidence cases. The evaluator
reports Citation precision/recall, claim Citation coverage, unsupported-Citation
rate, refusal correctness, answer completeness, and claim support separately.

No LLM judge is part of the `v1.0` release gate. A future judge may be added only
as a separately versioned evaluator after calibration against a human-reviewed
audit slice. Its model, prompt, decoding parameters, provider, and calibration
results must be persisted, and it must not replace the deterministic metrics.

## Consequences

- The baseline is reproducible, inexpensive, and auditable.
- Exact reviewed fact alternatives do not capture every valid paraphrase, so the
  metric is conservative and requires human calibration before headline use.
- Retrieval quality, Citation correctness, claim support, and refusal correctness
  remain separate; one aggregate score cannot hide a failure in another layer.
- Until the audit slice and provider-backed answer run exist, answer-quality
  metrics are implementation evidence rather than portfolio headline numbers.


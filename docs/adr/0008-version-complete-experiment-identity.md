# ADR 0008: Evaluation comparisons require version-complete experiment identity

- Status: Accepted (2026-08-30)
- Context: Checkpoints 2, 3, 4, and the portfolio release

## Context

Retrieval and answer-quality results are easy to miscompare. A run can retain the
same friendly name while its corpus, split, chunk boundaries, model, filter,
reranker, cache state, or pricing ledger changes. A timestamped run ID is useful
for storage, but it does not describe the experiment that produced the result.

## Decision

Every persisted evaluation report carries a public configuration sufficient to
identify the experiment:

- evaluation-set version, reviewed split, and manifest identity;
- chunking version, chunking configuration hash, and tokenizer;
- embedding model and, when answers are enabled, generation model;
- retrieval mode, candidate limits, evidence limits and token budget, RRF
  parameters, and hierarchical-retrieval state;
- requested and effective lexical/reranker providers, including fallback state;
- query-planning state, metadata filters, and cold/warm cache condition;
- evaluator and pricing-ledger versions for answer/cost reports; and
- bootstrap sample count and seed for confidence intervals.

The release evidence envelope also records the source Git revision and the exact
command. Run IDs remain unique storage handles; they are not experiment
identities. Two runs are paired or compared only when all controlled identity
fields match, except for the one variable named by the experiment.

## Consequences

- Reports are longer, but a reviewer can reproduce and audit a result without
  guessing which defaults were active.
- A changed configuration creates a new experiment even if the human-readable
  label is unchanged.
- Legacy reports that lack a source revision or manifest identity can remain as
  diagnostics, but cannot gate a release or support a headline claim.
- Secrets, query text, answer text, and Document text are never identity fields.


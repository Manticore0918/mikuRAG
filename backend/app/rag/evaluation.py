"""Backward-compatible re-export of the executable evaluation metrics.

The metric implementation now lives in :mod:`app.evaluation.metrics`, which
adds Recall@1/5, NDCG@10, citation precision, filter correctness, latency
percentiles, and bootstrap confidence intervals. This module keeps the
historical import path working for callers inside and outside the project.
"""

from app.evaluation.metrics import (
    RetrievalEvaluationCase,
    RetrievalEvaluationMetrics,
    RetrievalEvaluationObservation,
    bootstrap_confidence_intervals,
    compare_evaluations,
    evaluate_retrieval,
    load_evaluation_observations,
    load_evaluation_set,
)

__all__ = [
    "RetrievalEvaluationCase",
    "RetrievalEvaluationMetrics",
    "RetrievalEvaluationObservation",
    "bootstrap_confidence_intervals",
    "compare_evaluations",
    "evaluate_retrieval",
    "load_evaluation_observations",
    "load_evaluation_set",
]

"""Retrieval and grounded-answer metrics for executable evaluation runs.

Checkpoint 2 expands the historical ``app.rag.evaluation`` metrics with graded
Recall@1/5/10, NDCG@10, citation precision, filter correctness, latency
percentiles, and bootstrap confidence intervals over cases. ``app.rag.evaluation``
re-exports the public names so callers of the old module keep working.
"""

import json
import math
import random
from dataclasses import dataclass, field, fields
from pathlib import Path
from statistics import fmean

_SUPPORTED_SPLITS = {"train", "dev", "test"}


@dataclass(frozen=True)
class RetrievalEvaluationCase:
    case_id: str
    category: str
    query: str
    relevant_passage_ids: tuple[str, ...]
    required_passage_ids: tuple[str, ...]
    expected_citation_pages: tuple[int, ...]
    expects_supported_answer: bool
    split: str = "train"
    relevance_grades: dict[str, int] = field(default_factory=dict)
    filters: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalEvaluationObservation:
    case_id: str
    retrieved_passage_ids: tuple[str, ...]
    reranked_passage_ids: tuple[str, ...]
    citation_pages: tuple[int, ...]
    answer_faithful: bool
    retrieval_latency_ms: float
    end_to_end_latency_ms: float
    evidence_tokens: int
    used_summary_path: bool | None = None
    filter_correct: bool | None = None


@dataclass(frozen=True)
class RetrievalEvaluationMetrics:
    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    recall_after_reranking: float
    mean_reciprocal_rank: float
    ndcg_at_10: float
    citation_page_accuracy: float
    citation_precision: float
    answer_faithfulness: float
    all_required_passages_rate: float
    filter_correctness: float | None
    mean_retrieval_latency_ms: float
    retrieval_latency_p95_ms: float
    mean_end_to_end_latency_ms: float
    end_to_end_latency_p95_ms: float
    mean_evidence_tokens: float


def load_evaluation_set(path: Path) -> tuple[str, list[RetrievalEvaluationCase]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("The retrieval evaluation set requires a version")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("The retrieval evaluation set requires cases")

    cases: list[RetrievalEvaluationCase] = []
    for raw_case in raw_cases:
        grades_raw = raw_case.get("relevance_grades") or {}
        cases.append(
            RetrievalEvaluationCase(
                case_id=str(raw_case["case_id"]),
                category=str(raw_case["category"]),
                query=str(raw_case["query"]),
                relevant_passage_ids=tuple(raw_case["relevant_passage_ids"]),
                required_passage_ids=tuple(raw_case["required_passage_ids"]),
                expected_citation_pages=tuple(raw_case["expected_citation_pages"]),
                expects_supported_answer=bool(raw_case["expects_supported_answer"]),
                split=str(raw_case.get("split") or "train"),
                relevance_grades={
                    str(passage_id): int(grade)
                    for passage_id, grade in grades_raw.items()
                },
            )
        )
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Retrieval evaluation case IDs must be unique")
    return version, cases


def load_evaluation_observations(
    path: Path,
) -> tuple[str, list[RetrievalEvaluationObservation]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = payload.get("evaluation_set_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("Evaluation observations require evaluation_set_version")
    raw_observations = payload.get("observations")
    if not isinstance(raw_observations, list):
        raise ValueError("Evaluation observations require an observations list")
    observations = [
        RetrievalEvaluationObservation(
            case_id=str(item["case_id"]),
            retrieved_passage_ids=tuple(item["retrieved_passage_ids"]),
            reranked_passage_ids=tuple(item["reranked_passage_ids"]),
            citation_pages=tuple(item["citation_pages"]),
            answer_faithful=bool(item["answer_faithful"]),
            retrieval_latency_ms=float(item["retrieval_latency_ms"]),
            end_to_end_latency_ms=float(item["end_to_end_latency_ms"]),
            evidence_tokens=int(item["evidence_tokens"]),
            used_summary_path=(
                bool(item["used_summary_path"])
                if item.get("used_summary_path") is not None
                else None
            ),
            filter_correct=(
                bool(item["filter_correct"])
                if item.get("filter_correct") is not None
                else None
            ),
        )
        for item in raw_observations
    ]
    if len({item.case_id for item in observations}) != len(observations):
        raise ValueError("Evaluation observations must have unique case IDs")
    return version, observations


def evaluate_retrieval(
    cases: list[RetrievalEvaluationCase],
    observations: list[RetrievalEvaluationObservation],
) -> RetrievalEvaluationMetrics:
    observations_by_id = {observation.case_id: observation for observation in observations}
    if len(observations_by_id) != len(observations):
        raise ValueError("Retrieval evaluation observations must have unique case IDs")
    if set(observations_by_id) != {case.case_id for case in cases}:
        raise ValueError("Every evaluation case requires exactly one observation")

    vectors = _per_case_metric_vectors(cases, observations)
    return RetrievalEvaluationMetrics(
        recall_at_1=fmean(vectors["recall_at_1"]),
        recall_at_5=fmean(vectors["recall_at_5"]),
        recall_at_10=fmean(vectors["recall_at_10"]),
        recall_after_reranking=fmean(vectors["recall_after_reranking"]),
        mean_reciprocal_rank=fmean(vectors["mean_reciprocal_rank"]),
        ndcg_at_10=fmean(vectors["ndcg_at_10"]),
        citation_page_accuracy=fmean(vectors["citation_page_accuracy"]),
        citation_precision=fmean(vectors["citation_precision"]),
        answer_faithfulness=fmean(vectors["answer_faithfulness"]),
        all_required_passages_rate=fmean(vectors["all_required_passages_rate"]),
        filter_correctness=_mean_defined(vectors["filter_correctness"]),
        mean_retrieval_latency_ms=fmean(vectors["retrieval_latency_ms"]),
        retrieval_latency_p95_ms=_percentile(sorted(vectors["retrieval_latency_ms"]), 0.95),
        mean_end_to_end_latency_ms=fmean(vectors["end_to_end_latency_ms"]),
        end_to_end_latency_p95_ms=_percentile(sorted(vectors["end_to_end_latency_ms"]), 0.95),
        mean_evidence_tokens=fmean(vectors["evidence_tokens"]),
    )


def compare_evaluations(
    baseline: RetrievalEvaluationMetrics,
    candidate: RetrievalEvaluationMetrics,
) -> dict[str, float]:
    return {
        field.name: getattr(candidate, field.name) - getattr(baseline, field.name)
        for field in fields(RetrievalEvaluationMetrics)
    }


_MEAN_VECTOR_METRICS: dict[str, str] = {
    "recall_at_1": "recall_at_1",
    "recall_at_5": "recall_at_5",
    "recall_at_10": "recall_at_10",
    "recall_after_reranking": "recall_after_reranking",
    "mean_reciprocal_rank": "mean_reciprocal_rank",
    "ndcg_at_10": "ndcg_at_10",
    "citation_page_accuracy": "citation_page_accuracy",
    "citation_precision": "citation_precision",
    "answer_faithfulness": "answer_faithfulness",
    "all_required_passages_rate": "all_required_passages_rate",
    "filter_correctness": "filter_correctness",
    "mean_retrieval_latency_ms": "retrieval_latency_ms",
    "mean_end_to_end_latency_ms": "end_to_end_latency_ms",
    "mean_evidence_tokens": "evidence_tokens",
}

_P95_VECTOR_METRICS: dict[str, str] = {
    "retrieval_latency_p95_ms": "retrieval_latency_ms",
    "end_to_end_latency_p95_ms": "end_to_end_latency_ms",
}


def bootstrap_confidence_intervals(
    cases: list[RetrievalEvaluationCase],
    observations: list[RetrievalEvaluationObservation],
    *,
    samples: int = 2_000,
    alpha: float = 0.05,
    seed: int | None = 0,
) -> dict[str, dict[str, float]]:
    """Bootstrap 100*(1-alpha)% percentile intervals for every reported metric.

    Mean-based metrics resample per-case vectors; latency percentiles are
    recomputed on each resampled latency vector. Results are reproducible for a
    fixed ``seed``.
    """
    vectors = _per_case_metric_vectors(cases, observations)
    size = len(cases)
    rng = random.Random(seed)
    metric_names = [*_MEAN_VECTOR_METRICS, *_P95_VECTOR_METRICS]
    sampled = {name: [] for name in metric_names}
    for _ in range(samples):
        indices = [rng.randrange(size) for _ in range(size)]
        for metric, vector_name in _MEAN_VECTOR_METRICS.items():
            values = [
                vectors[vector_name][index]
                for index in indices
                if vectors[vector_name][index] is not None
            ]
            if values:
                sampled[metric].append(fmean(values))
        for metric, vector_name in _P95_VECTOR_METRICS.items():
            values = [
                vectors[vector_name][index]
                for index in indices
                if vectors[vector_name][index] is not None
            ]
            if values:
                sampled[metric].append(_percentile(sorted(values), 0.95))
    intervals: dict[str, dict[str, float]] = {}
    for name in metric_names:
        boot = sorted(sampled[name])
        if not boot:
            intervals[name] = {"mean": 0.0, "ci_low": 0.0, "ci_high": 0.0}
            continue
        intervals[name] = {
            "mean": fmean(boot),
            "ci_low": _percentile(boot, alpha / 2),
            "ci_high": _percentile(boot, 1 - alpha / 2),
        }
    return intervals


def _per_case_metric_vectors(
    cases: list[RetrievalEvaluationCase],
    observations: list[RetrievalEvaluationObservation],
) -> dict[str, list[float | None]]:
    observations_by_id = {observation.case_id: observation for observation in observations}
    vectors: dict[str, list[float | None]] = {
        "recall_at_1": [],
        "recall_at_5": [],
        "recall_at_10": [],
        "recall_after_reranking": [],
        "mean_reciprocal_rank": [],
        "ndcg_at_10": [],
        "citation_page_accuracy": [],
        "citation_precision": [],
        "answer_faithfulness": [],
        "all_required_passages_rate": [],
        "filter_correctness": [],
        "retrieval_latency_ms": [],
        "end_to_end_latency_ms": [],
        "evidence_tokens": [],
    }
    for case in cases:
        observation = observations_by_id[case.case_id]
        relevant = set(case.relevant_passage_ids)
        required = set(case.required_passage_ids)
        reranked = observation.reranked_passage_ids
        expected_pages = set(case.expected_citation_pages)
        cited_pages = set(observation.citation_pages)
        grades = case.relevance_grades or {
            passage_id: (3 if passage_id in required else 1)
            for passage_id in case.relevant_passage_ids
        }
        vectors["recall_at_1"].append(_recall(relevant, reranked[:1]))
        vectors["recall_at_5"].append(_recall(relevant, reranked[:5]))
        vectors["recall_at_10"].append(_recall(relevant, reranked[:10]))
        vectors["recall_after_reranking"].append(_recall(relevant, reranked))
        vectors["mean_reciprocal_rank"].append(_reciprocal_rank(relevant, reranked[:10]))
        vectors["ndcg_at_10"].append(_ndcg_at_10(grades, reranked))
        vectors["citation_page_accuracy"].append(
            _recall(expected_pages, observation.citation_pages)
        )
        vectors["citation_precision"].append(_citation_precision(expected_pages, cited_pages))
        vectors["answer_faithfulness"].append(float(observation.answer_faithful))
        vectors["all_required_passages_rate"].append(float(required <= set(reranked)))
        vectors["filter_correctness"].append(
            bool(observation.filter_correct)
            if observation.filter_correct is not None and case.filters
            else None
        )
        vectors["retrieval_latency_ms"].append(observation.retrieval_latency_ms)
        vectors["end_to_end_latency_ms"].append(observation.end_to_end_latency_ms)
        vectors["evidence_tokens"].append(float(observation.evidence_tokens))
    return vectors


def _recall(expected: set[object], actual: tuple[object, ...]) -> float:
    if not expected:
        return 1.0
    return len(expected & set(actual)) / len(expected)


def _reciprocal_rank(relevant: set[str], ranked: tuple[str, ...]) -> float:
    if not relevant:
        return 1.0 if not ranked else 0.0
    for rank, passage_id in enumerate(ranked, start=1):
        if passage_id in relevant:
            return 1 / rank
    return 0.0


def _ndcg_at_10(grades: dict[str, int], ranked: tuple[str, ...]) -> float:
    ideal = sorted(grades.values(), reverse=True)
    if not ideal:
        return 1.0
    dcg = sum(
        grades.get(passage_id, 0) / math.log2(rank + 1)
        for rank, passage_id in enumerate(ranked[:10], start=1)
    )
    idcg = sum(rel / math.log2(index + 1) for index, rel in enumerate(ideal, start=1))
    return dcg / idcg if idcg else 0.0


def _citation_precision(expected: set[int], cited: set[int]) -> float:
    if not cited:
        return 0.0 if expected else 1.0
    return len(expected & cited) / len(cited)


def _mean_defined(values: list[float | None]) -> float | None:
    defined = [value for value in values if value is not None]
    return fmean(defined) if defined else None


def _percentile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    return sorted_values[lower] * (upper - position) + sorted_values[upper] * (
        position - lower
    )

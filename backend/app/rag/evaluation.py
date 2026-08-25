import json
from dataclasses import dataclass, fields
from pathlib import Path
from statistics import fmean


@dataclass(frozen=True)
class RetrievalEvaluationCase:
    case_id: str
    category: str
    query: str
    relevant_passage_ids: tuple[str, ...]
    required_passage_ids: tuple[str, ...]
    expected_citation_pages: tuple[int, ...]
    expects_supported_answer: bool


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


@dataclass(frozen=True)
class RetrievalEvaluationMetrics:
    recall_at_10: float
    recall_after_reranking: float
    mean_reciprocal_rank: float
    citation_page_accuracy: float
    answer_faithfulness: float
    all_required_passages_rate: float
    mean_retrieval_latency_ms: float
    mean_end_to_end_latency_ms: float
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
        cases.append(
            RetrievalEvaluationCase(
                case_id=str(raw_case["case_id"]),
                category=str(raw_case["category"]),
                query=str(raw_case["query"]),
                relevant_passage_ids=tuple(raw_case["relevant_passage_ids"]),
                required_passage_ids=tuple(raw_case["required_passage_ids"]),
                expected_citation_pages=tuple(raw_case["expected_citation_pages"]),
                expects_supported_answer=bool(raw_case["expects_supported_answer"]),
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

    recall_at_10: list[float] = []
    recall_after_reranking: list[float] = []
    reciprocal_ranks: list[float] = []
    citation_accuracy: list[float] = []
    faithful: list[float] = []
    all_required: list[float] = []

    for case in cases:
        observation = observations_by_id[case.case_id]
        relevant = set(case.relevant_passage_ids)
        required = set(case.required_passage_ids)
        retrieved = observation.retrieved_passage_ids[:10]
        reranked = observation.reranked_passage_ids
        recall_at_10.append(_recall(relevant, retrieved))
        recall_after_reranking.append(_recall(relevant, reranked))
        reciprocal_ranks.append(_reciprocal_rank(relevant, reranked))
        citation_accuracy.append(
            _recall(set(case.expected_citation_pages), observation.citation_pages)
        )
        faithful.append(float(observation.answer_faithful))
        all_required.append(float(required <= set(reranked)))

    return RetrievalEvaluationMetrics(
        recall_at_10=fmean(recall_at_10),
        recall_after_reranking=fmean(recall_after_reranking),
        mean_reciprocal_rank=fmean(reciprocal_ranks),
        citation_page_accuracy=fmean(citation_accuracy),
        answer_faithfulness=fmean(faithful),
        all_required_passages_rate=fmean(all_required),
        mean_retrieval_latency_ms=fmean(
            observation.retrieval_latency_ms for observation in observations
        ),
        mean_end_to_end_latency_ms=fmean(
            observation.end_to_end_latency_ms for observation in observations
        ),
        mean_evidence_tokens=fmean(
            observation.evidence_tokens for observation in observations
        ),
    )


def compare_evaluations(
    baseline: RetrievalEvaluationMetrics,
    candidate: RetrievalEvaluationMetrics,
) -> dict[str, float]:
    return {
        field.name: getattr(candidate, field.name) - getattr(baseline, field.name)
        for field in fields(RetrievalEvaluationMetrics)
    }


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

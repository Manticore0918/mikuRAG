"""Deterministic, versioned answer-faithfulness scoring.

The evaluator deliberately uses reviewed facts and evidence IDs instead of an
LLM judge.  It is cheap, reproducible, and safe for private corpora.  A learned
judge can be added later as a separately versioned evaluator without changing
these baseline metrics.
"""

from dataclasses import asdict, dataclass
from statistics import fmean

from app.evaluation.datasets import ExecutableEvaluationCase

EVALUATOR_NAME = "deterministic_claim_support"
EVALUATOR_VERSION = "1.0.0"


@dataclass(frozen=True)
class FaithfulnessResult:
    evaluator: str
    evaluator_version: str
    citation_precision: float
    citation_recall: float
    claim_citation_coverage: float
    unsupported_citation_rate: float
    refusal_correctness: float
    answer_completeness: float
    claim_support: float
    expected_claim_count: int
    supported_claim_count: int
    human_audit_required: bool = False

    def as_dict(self) -> dict[str, str | float | int | bool]:
        return asdict(self)


def evaluate_answer_faithfulness(
    case: ExecutableEvaluationCase,
    *,
    content: str,
    outcome: str,
    used_passage_ids: tuple[str, ...],
) -> FaithfulnessResult:
    """Score one answer against reviewed facts, claims, and required evidence."""

    used = set(used_passage_ids)
    relevant = set(case.relevant_passage_ids)
    required = set(case.required_evidence)
    normalized_content = content.casefold()

    citation_precision = _precision(relevant, used)
    citation_recall = _recall(required, used)
    unsupported_citation_rate = (
        len(used - relevant) / len(used) if used else 0.0
    )
    refusal_correctness = float(_refusal_is_correct(case, outcome, used))

    facts = case.acceptable_answer_facts
    fact_scores = [
        float(any(alternative.casefold() in normalized_content for alternative in fact))
        for fact in facts
    ]
    answer_completeness = fmean(fact_scores) if fact_scores else refusal_correctness

    claim_citation_scores: list[float] = []
    claim_support_scores: list[float] = []
    for claim in case.expected_claims:
        fact_present = any(
            phrase.casefold() in normalized_content
            for phrase in claim.acceptable_answer_facts
        )
        evidence_present = set(claim.required_evidence) <= used
        claim_citation_scores.append(float(evidence_present))
        claim_support_scores.append(float(fact_present and evidence_present))
    claim_citation_coverage = (
        fmean(claim_citation_scores) if claim_citation_scores else refusal_correctness
    )
    claim_support = (
        fmean(claim_support_scores) if claim_support_scores else refusal_correctness
    )
    supported_claim_count = sum(score == 1.0 for score in claim_support_scores)

    return FaithfulnessResult(
        evaluator=EVALUATOR_NAME,
        evaluator_version=EVALUATOR_VERSION,
        citation_precision=citation_precision,
        citation_recall=citation_recall,
        claim_citation_coverage=claim_citation_coverage,
        unsupported_citation_rate=unsupported_citation_rate,
        refusal_correctness=refusal_correctness,
        answer_completeness=answer_completeness,
        claim_support=claim_support,
        expected_claim_count=len(claim_support_scores),
        supported_claim_count=supported_claim_count,
    )


def aggregate_faithfulness(
    results: list[dict[str, object]],
) -> dict[str, object] | None:
    if not results:
        return None
    metric_names = (
        "citation_precision",
        "citation_recall",
        "claim_citation_coverage",
        "unsupported_citation_rate",
        "refusal_correctness",
        "answer_completeness",
        "claim_support",
    )
    aggregate: dict[str, object] = {
        "evaluator": EVALUATOR_NAME,
        "evaluator_version": EVALUATOR_VERSION,
        "case_count": len(results),
        "human_audit_rate": fmean(
            float(bool(item.get("human_audit_required"))) for item in results
        ),
    }
    for name in metric_names:
        aggregate[name] = fmean(float(item[name]) for item in results)
    return aggregate


def _refusal_is_correct(
    case: ExecutableEvaluationCase,
    outcome: str,
    used: set[str],
) -> bool:
    if not case.refusal_expected:
        return outcome == "grounded_answer"
    if case.conflicting_evidence:
        return outcome == "conflicting_evidence" and set(case.required_evidence) <= used
    return outcome == "insufficient_evidence" and not used


def _precision(expected: set[str], actual: set[str]) -> float:
    if not actual:
        return 1.0 if not expected else 0.0
    return len(expected & actual) / len(actual)


def _recall(expected: set[str], actual: set[str]) -> float:
    if not expected:
        return 1.0
    return len(expected & actual) / len(expected)

from app.evaluation.datasets import EvaluationExpectedClaim, ExecutableEvaluationCase
from app.evaluation.faithfulness import evaluate_answer_faithfulness


def _case(**overrides) -> ExecutableEvaluationCase:
    values = {
        "case_id": "claim-case",
        "category": "narrow_fact",
        "query": "What is the limit?",
        "relevant_passage_ids": ("p1", "p2"),
        "required_passage_ids": ("p1",),
        "expected_citation_pages": (),
        "expected_answer_terms": ("five days",),
        "expects_supported_answer": True,
        "filters": {},
        "expected_claims": (
            EvaluationExpectedClaim(
                claim_id="claim-one",
                acceptable_answer_facts=("five days", "5 days"),
                required_evidence=("p1",),
            ),
        ),
        "acceptable_answer_facts": (("five days", "5 days"),),
        "required_evidence": ("p1",),
    }
    values.update(overrides)
    return ExecutableEvaluationCase(**values)


def test_supported_claim_metrics_separate_completeness_and_citations() -> None:
    result = evaluate_answer_faithfulness(
        _case(),
        content="The limit is five days. [1]",
        outcome="grounded_answer",
        used_passage_ids=("p1", "noise"),
    )

    assert result.answer_completeness == 1
    assert result.citation_recall == 1
    assert result.citation_precision == 0.5
    assert result.unsupported_citation_rate == 0.5
    assert result.claim_citation_coverage == 1


def test_unsupported_case_scores_correct_refusal_without_citations() -> None:
    result = evaluate_answer_faithfulness(
        _case(
            category="unsupported",
            relevant_passage_ids=(),
            required_passage_ids=(),
            expected_answer_terms=(),
            expects_supported_answer=False,
            expected_claims=(),
            acceptable_answer_facts=(),
            required_evidence=(),
            refusal_expected=True,
        ),
        content="I cannot answer reliably.",
        outcome="insufficient_evidence",
        used_passage_ids=(),
    )

    assert result.refusal_correctness == 1
    assert result.answer_completeness == 1
    assert result.claim_support == 1


def test_conflicting_case_requires_both_sides_as_evidence() -> None:
    case = _case(
        category="conflicting_evidence",
        relevant_passage_ids=("p1", "p2"),
        required_passage_ids=("p1", "p2"),
        expects_supported_answer=False,
        expected_claims=(),
        acceptable_answer_facts=(),
        required_evidence=("p1", "p2"),
        refusal_expected=True,
        conflicting_evidence=True,
    )

    result = evaluate_answer_faithfulness(
        case,
        content="The Documents conflict.",
        outcome="conflicting_evidence",
        used_passage_ids=("p1",),
    )

    assert result.refusal_correctness == 0
    assert result.citation_recall == 0.5

"""Tests for the reviewed gold_v1 evaluation corpus and its runner scoring."""

import json
import shutil
from pathlib import Path

import pytest

from app.evaluation.datasets import load_executable_dataset, passage_matches_locator
from app.evaluation.runner import _answer_is_faithful
from app.ingestion.chunking import chunk_sections
from app.ingestion.extraction import extract_document
from app.ingestion.normalization import normalize_document

GOLD = (
    Path(__file__).parents[1]
    / "evaluation"
    / "corpus"
    / "gold_v1"
    / "manifest.json"
)

MEDIA = {
    ".md": "text/markdown",
    ".py": "text/x-python",
    ".ts": "text/typescript",
    ".html": "text/html",
    ".pdf": "application/pdf",
}

EXPECTED_CATEGORY_COUNTS = {
    "exact_identifier": 8,
    "semantic_paraphrase": 7,
    "cross_page": 6,
    "multi_section": 5,
    "code_symbol": 8,
    "html_heading_list": 7,
    "multi_document": 7,
    "metadata_filtered": 5,
    "broad_summary": 3,
    "unsupported": 4,
    "conflicting_evidence": 4,
}


def _chunk_locators(document) -> list[dict]:
    media_type = MEDIA[document.path.suffix]
    extracted = extract_document(
        document.path,
        media_type,
        max_pages=500,
        source_path=f"evaluation/gold_v1/{document.relative_path}",
    )
    normalized = normalize_document(extracted)
    return [
        dict(chunk.locator)
        for chunk in chunk_sections(
            normalized.sections,
            target_characters=800,
            overlap_characters=100,
        )
    ]


def test_gold_manifest_is_schema_two_and_reviewed() -> None:
    dataset = load_executable_dataset(GOLD)

    assert dataset.schema_version == 2
    assert dataset.version == "gold_v1"
    assert dataset.review_status == "reviewed"
    assert dataset.headline_eligible is False
    assert dataset.license_id == "CC0-1.0"
    assert dataset.provenance == "synthetic"
    assert dataset.contains_sensitive_data is False
    assert len(dataset.documents) == 14
    assert len(dataset.cases) >= 60


def test_gold_cases_are_individually_marked_reviewed() -> None:
    payload = json.loads(GOLD.read_text(encoding="utf-8"))
    assert payload["review_status"] == "reviewed"
    assert payload["headline_eligible"] is False
    for case in payload["cases"]:
        assert case.get("reviewed") is True, case["case_id"]


def test_gold_set_contains_reviewed_follow_up_rewrite_cases() -> None:
    dataset = load_executable_dataset(GOLD)
    follow_ups = [case for case in dataset.cases if case.history]

    assert len(follow_ups) >= 3
    assert {case.split for case in follow_ups} >= {"dev", "test"}
    assert any("QPX-731" in case.query for case in follow_ups)


def test_gold_category_coverage_matches_design() -> None:
    dataset = load_executable_dataset(GOLD)
    counts = {}
    for case in dataset.cases:
        counts[case.category] = counts.get(case.category, 0) + 1
    assert counts == EXPECTED_CATEGORY_COUNTS


def test_gold_qrels_resolve_and_are_consistent() -> None:
    dataset = load_executable_dataset(GOLD)
    for case in dataset.cases:
        required = set(case.required_passage_ids)
        relevant = set(case.relevant_passage_ids)
        assert required <= relevant, case.case_id
        if not case.expects_supported_answer:
            if case.required_passage_ids:
                assert case.category == "conflicting_evidence", case.case_id
                assert len(case.required_passage_ids) >= 2, case.case_id
            else:
                assert case.category == "unsupported", case.case_id
                assert not case.relevant_passage_ids, case.case_id


def test_gold_splits_cover_train_dev_test_with_category_coverage() -> None:
    dataset = load_executable_dataset(GOLD)
    counts = {"train": 0, "dev": 0, "test": 0}
    test_categories = set()
    for case in dataset.cases:
        assert case.split in counts, case.case_id
        counts[case.split] += 1
        if case.split == "test":
            test_categories.add(case.category)
    assert counts == {"train": 35, "dev": 16, "test": 13}
    assert test_categories == set(EXPECTED_CATEGORY_COUNTS)


def test_gold_relevance_grades_follow_grading_scheme() -> None:
    dataset = load_executable_dataset(GOLD)
    for case in dataset.cases:
        required = set(case.required_passage_ids)
        relevant = set(case.relevant_passage_ids)
        assert set(case.relevance_grades) == relevant, case.case_id
        for passage_id in required:
            assert case.relevance_grades[passage_id] == 3, case.case_id
        for passage_id in relevant - required:
            assert case.relevance_grades[passage_id] == 1, case.case_id


def test_gold_metadata_filters_are_respected_by_qrels() -> None:
    dataset = load_executable_dataset(GOLD)
    passage_documents = {
        passage.passage_id: document
        for document in dataset.documents
        for passage in document.passages
    }
    keys = set()
    for case in dataset.cases:
        if not case.filters:
            continue
        keys.update(case.filters)
        for passage_id in case.relevant_passage_ids:
            document = passage_documents[passage_id]
            if "document_ids" in case.filters:
                assert document.document_id in case.filters["document_ids"], case.case_id
            if "tags" in case.filters:
                assert set(case.filters["tags"]) <= set(document.tags), case.case_id
            if "source_kinds" in case.filters:
                assert document.source_kind in case.filters["source_kinds"], case.case_id
            if "languages" in case.filters:
                assert document.language in case.filters["languages"], case.case_id
    assert keys == {"document_ids", "tags", "source_kinds", "languages"}


@pytest.mark.parametrize("field", ["split", "relevance_grades"])
def test_v2_loader_rejects_case_missing_split_or_grades(tmp_path: Path, field: str) -> None:
    payload = json.loads(GOLD.read_text(encoding="utf-8"))
    del payload["cases"][0][field]
    corpus = tmp_path / "corpus"
    shutil.copytree(GOLD.parent, corpus, dirs_exist_ok=True)
    manifest = corpus / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_executable_dataset(manifest)


def test_v2_loader_rejects_case_with_grade_violating_scheme(tmp_path: Path) -> None:
    payload = json.loads(GOLD.read_text(encoding="utf-8"))
    case = payload["cases"][0]
    required = case["required_passage_ids"][0]
    case["relevance_grades"][required] = 1
    corpus = tmp_path / "corpus"
    shutil.copytree(GOLD.parent, corpus, dirs_exist_ok=True)
    manifest = corpus / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="graded 3"):
        load_executable_dataset(manifest)


def test_every_gold_passage_resolves_to_exactly_one_real_chunk() -> None:
    dataset = load_executable_dataset(GOLD)
    total = 0
    for document in dataset.documents:
        locators = _chunk_locators(document)
        assert len(locators) >= len(document.passages), document.document_id
        matches = {passage.passage_id: [] for passage in document.passages}
        for index, locator in enumerate(locators):
            for passage in document.passages:
                if passage_matches_locator(passage, locator):
                    matches[passage.passage_id].append(index)
        for passage in document.passages:
            assert len(matches[passage.passage_id]) == 1, (
                f"{document.document_id} {passage.passage_id} "
                f"matched {len(matches[passage.passage_id])} chunks"
            )
            total += 1
    assert total == sum(len(document.passages) for document in dataset.documents)


def test_answer_is_faithful_supported_case() -> None:
    faithful = dict(
        expects_supported_answer=True,
        required_passage_ids=("a", "b"),
        expected_terms_found=True,
        outcome="grounded_answer",
        content="SGD 1,200 with prior approval.",
        used_passages=("a", "b", "c"),
    )
    assert _answer_is_faithful(**faithful) is True
    missing_term = dict(faithful, expected_terms_found=False)
    assert _answer_is_faithful(**missing_term) is False
    missing_citation = dict(faithful, used_passages=("a",))
    assert _answer_is_faithful(**missing_citation) is False
    wrong_outcome = dict(faithful, outcome="conflicting_evidence")
    assert _answer_is_faithful(**wrong_outcome) is False


def test_answer_is_faithful_unsupported_case() -> None:
    faithful = dict(
        expects_supported_answer=False,
        required_passage_ids=(),
        expected_terms_found=True,
        outcome="insufficient_evidence",
        content="I cannot answer reliably from the available Documents.",
        used_passages=(),
    )
    assert _answer_is_faithful(**faithful) is True
    cited = dict(faithful, used_passages=("a",))
    assert _answer_is_faithful(**cited) is False
    answered = dict(faithful, outcome="grounded_answer")
    assert _answer_is_faithful(**answered) is False


def test_answer_is_faithful_conflicting_evidence_case() -> None:
    prefix = "I cannot answer reliably because the retrieved Documents conflict:"
    faithful = dict(
        expects_supported_answer=False,
        required_passage_ids=("trv-contractor-lodging", "faq-lodging-cap"),
        expected_terms_found=True,
        outcome="conflicting_evidence",
        content=(
            f"{prefix}\n\n- The contractor policy caps lodging at SGD 220 [1]\n\n"
            "- The FAQ reimburses up to SGD 180 per night [2]"
        ),
        used_passages=("trv-contractor-lodging", "faq-lodging-cap"),
    )
    assert _answer_is_faithful(**faithful) is True
    only_one_side = dict(faithful, used_passages=("trv-contractor-lodging",))
    assert _answer_is_faithful(**only_one_side) is False
    not_a_refusal = dict(faithful, outcome="grounded_answer")
    assert _answer_is_faithful(**not_a_refusal) is False
    refusal_missing_prefix = dict(faithful, content="I cannot decide between the two Documents.")
    assert _answer_is_faithful(**refusal_missing_prefix) is False

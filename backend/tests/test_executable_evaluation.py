import json
import uuid
from pathlib import Path

import pytest

from app.evaluation.contracts import (
    EvaluationAnswerRecord,
    EvaluationCaseRecord,
    EvaluationDocumentRecord,
    EvaluationRunOptions,
    EvaluationWorkspace,
)
from app.evaluation.datasets import load_executable_dataset
from app.evaluation.runner import (
    EvaluationExecutionError,
    EvaluationRuntimeError,
    execute_evaluation,
)

CORPUS = (
    Path(__file__).parents[1]
    / "evaluation"
    / "corpus"
    / "executable_v1"
    / "manifest.json"
)


class FakeRuntime:
    def __init__(
        self,
        *,
        fail_ingestion: bool = False,
        fail_answers: bool = False,
    ) -> None:
        self.fail_ingestion = fail_ingestion
        self.fail_answers = fail_answers
        self.actions: list[str] = []
        self.workspace = EvaluationWorkspace(
            knowledge_base_id=uuid.uuid4(),
            knowledge_base_name="Evaluation test",
            document_ids={"document": uuid.uuid4()},
            storage_keys=("evaluation/test/document",),
        )

    def public_configuration(self, *, include_answers: bool) -> dict[str, object]:
        return {"chunking_version": "legacy", "include_answers": include_answers}

    async def create_workspace(self, dataset, run_id):
        self.actions.append(f"create:{dataset.version}:{run_id}")
        return self.workspace

    async def wait_for_documents(
        self,
        workspace,
        dataset,
        *,
        timeout_seconds,
        poll_seconds,
    ):
        self.actions.append(f"wait:{timeout_seconds}:{poll_seconds}")
        records = tuple(
            EvaluationDocumentRecord(
                corpus_document_id=item.document_id,
                document_id=str(uuid.uuid4()),
                original_name=item.original_name,
                status="ready",
                ingestion_stage="ready",
                ingestion_progress=100,
                ingestion_attempts=1,
                parser_version="markdown_v2",
                chunking_version="legacy",
                chunk_count=1,
                warnings=(),
                safe_error=None,
            )
            for item in dataset.documents
        )
        if self.fail_ingestion:
            raise EvaluationRuntimeError(
                "Evaluation Ingestion failed for: fixture.md",
                documents=records,
            )
        return records

    def ingestion_statistics(self) -> dict[str, object]:
        return {
            "ingestion_duration_ms": 0.0,
            "embedding_input_count": 4,
            "total_chunk_count": 4,
            "storage_estimate_bytes": 4096,
        }

    async def warmup(self) -> None:
        self.actions.append("warmup")

    async def run_case(self, workspace, case, dataset, *, include_answers):
        self.actions.append(f"case:{case.case_id}:{include_answers}")
        passages = case.required_passage_ids if case.expects_supported_answer else ()
        answer = (
            EvaluationAnswerRecord(
                content="Grounded fixture answer",
                outcome=(
                    "generation_error"
                    if self.fail_answers
                    else (
                        "grounded_answer"
                        if case.expects_supported_answer
                        else "insufficient_evidence"
                    )
                ),
                used_passage_ids=passages,
                expected_terms_found=True,
                usage={"output_tokens": 4},
                safe_error=("Fixture generation failed" if self.fail_answers else None),
            )
            if include_answers
            else None
        )
        return EvaluationCaseRecord(
            case_id=case.case_id,
            category=case.category,
            query=case.query,
            expects_supported_answer=case.expects_supported_answer,
            relevant_passage_ids=case.relevant_passage_ids,
            required_passage_ids=case.required_passage_ids,
            expected_citation_pages=case.expected_citation_pages,
            filters=case.filters,
            retrieved_passage_ids=passages,
            reranked_passage_ids=passages,
            citation_pages=case.expected_citation_pages,
            sufficient=case.expects_supported_answer,
            retrieval_passed=True,
            answer_faithful=not self.fail_answers,
            retrieval_latency_ms=8.0,
            end_to_end_latency_ms=12.0,
            evidence_tokens=40 if passages else 0,
            used_summary_path=False,
            retrieval_metrics={"retrieval_duration_ms": 8.0},
            evidence=(),
            answer=answer,
        )

    async def cleanup(self, workspace):
        self.actions.append("cleanup")


def test_versioned_executable_corpus_has_stable_documents_and_cases() -> None:
    dataset = load_executable_dataset(CORPUS)

    assert dataset.version == "executable_v1"
    assert dataset.license_id == "CC0-1.0"
    assert dataset.provenance == "synthetic"
    assert dataset.contains_sensitive_data is False
    assert len(dataset.documents) == 4
    assert len(dataset.cases) == 5
    passages = {passage.passage_id for item in dataset.documents for passage in item.passages}
    assert passages == {
        "hr-leave-p3",
        "security-codes-p8",
        "finance-assets-p12",
        "security-access-p14-15",
    }
    locators = {passage.locator_id for item in dataset.documents for passage in item.passages}
    assert locators == {
        "markdown:leave-policy#annual-leave-carry-over",
        "markdown:security-incident#incident-code-sec-417",
        "markdown:asset-approval#high-value-equipment-approval",
        "markdown:temporary-access#temporary-administrator-access",
    }
    assert any(not item.expects_supported_answer for item in dataset.cases)


@pytest.mark.asyncio
async def test_executable_runner_writes_raw_and_aggregate_artifacts(tmp_path: Path) -> None:
    runtime = FakeRuntime()

    result = await execute_evaluation(
        EvaluationRunOptions(
            dataset_path=CORPUS,
            output_dir=tmp_path,
            include_answers=True,
            run_id="test-run-0001",
        ),
        runtime=runtime,
    )

    assert result.run.status == "completed"
    assert result.run.knowledge_base_cleaned_up is True
    assert result.aggregate["retrieval_pass_rate"] == 1
    assert result.aggregate["metrics"]["answer_faithfulness"] == 1
    assert result.run.embedding_input_count == 4
    assert result.run.total_chunk_count == 4
    assert result.run.storage_estimate_bytes == 4096
    assert result.run.ingestion_duration_ms == 0.0
    assert result.run.configuration["bootstrap_samples"] == 2000
    assert result.run.configuration["bootstrap_seed"] == 0
    assert result.artifacts.raw_json.is_file()
    assert result.artifacts.report_json.is_file()
    assert result.artifacts.report_markdown.is_file()
    raw = json.loads(result.artifacts.raw_json.read_text(encoding="utf-8"))
    assert raw["evaluation_set_version"] == "executable_v1"
    assert len(raw["documents"]) == 4
    assert len(raw["cases"]) == 5
    assert raw["embedding_input_count"] == 4
    assert runtime.actions.index("warmup") < next(
        index for index, action in enumerate(runtime.actions) if action.startswith("case:")
    )
    assert runtime.actions[-1] == "cleanup"


@pytest.mark.asyncio
async def test_failed_ingestion_still_writes_artifacts_and_cleans_up(tmp_path: Path) -> None:
    runtime = FakeRuntime(fail_ingestion=True)

    with pytest.raises(EvaluationExecutionError) as raised:
        await execute_evaluation(
            EvaluationRunOptions(
                dataset_path=CORPUS,
                output_dir=tmp_path,
                run_id="test-run-failed",
            ),
            runtime=runtime,
        )

    result = raised.value.result
    assert result.run.status == "failed"
    assert result.run.knowledge_base_cleaned_up is True
    assert result.run.safe_error == "Evaluation Ingestion failed for: fixture.md"
    assert result.artifacts.raw_json.is_file()
    assert result.artifacts.report_json.is_file()
    assert runtime.actions[-1] == "cleanup"


@pytest.mark.asyncio
async def test_failed_answers_are_all_recorded_before_run_fails(tmp_path: Path) -> None:
    runtime = FakeRuntime(fail_answers=True)

    with pytest.raises(EvaluationExecutionError) as raised:
        await execute_evaluation(
            EvaluationRunOptions(
                dataset_path=CORPUS,
                output_dir=tmp_path,
                include_answers=True,
                run_id="test-run-answer-failure",
            ),
            runtime=runtime,
        )

    result = raised.value.result
    assert result.run.status == "failed"
    assert len(result.run.cases) == 5
    assert result.aggregate["answer_case_count"] == 5
    assert result.aggregate["answer_failure_count"] == 5
    assert result.aggregate["metrics"]["answer_faithfulness"] == 0
    assert "5 evaluation case(s)" in (result.run.safe_error or "")
    assert result.run.knowledge_base_cleaned_up is True
    assert result.artifacts.raw_json.is_file()
    assert result.artifacts.report_json.is_file()
    assert runtime.actions[-1] == "cleanup"


@pytest.mark.asyncio
async def test_executable_runner_accepts_a_pre_filtered_dataset(tmp_path: Path) -> None:
    from dataclasses import replace

    from app.evaluation.datasets import load_executable_dataset

    runtime = FakeRuntime()
    dataset = load_executable_dataset(CORPUS)
    filtered = replace(dataset, cases=dataset.cases[:2])

    result = await execute_evaluation(
        EvaluationRunOptions(
            dataset_path=CORPUS,
            output_dir=tmp_path,
            run_id="test-run-filtered",
            target_chunking_version="token_recursive_v1",
        ),
        runtime=runtime,
        dataset=filtered,
    )

    assert result.run.status == "completed"
    assert len(result.run.cases) == 2
    assert result.run.evaluation_set_version == "executable_v1"
    assert result.run.configuration["chunking_version"] == "legacy"

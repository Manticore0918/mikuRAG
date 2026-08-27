import asyncio
import logging
import re
import shutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from time import perf_counter
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.database import session_factory
from app.evaluation.contracts import (
    EvaluationAnswerRecord,
    EvaluationCaseRecord,
    EvaluationDocumentRecord,
    EvaluationEvidenceRecord,
    EvaluationExecutionResult,
    EvaluationRunOptions,
    EvaluationRunRecord,
    EvaluationWorkspace,
)
from app.evaluation.datasets import (
    ExecutableEvaluationCase,
    ExecutableEvaluationDataset,
    load_executable_dataset,
)
from app.evaluation.reporting import build_aggregate_report, write_evaluation_artifacts
from app.ingestion.dispatch import enqueue_ingestion
from app.ingestion.embeddings import embed_texts
from app.ingestion.storage import remove_stored_file_sync, storage_path
from app.models import Chunk, ChunkLevel, Document, DocumentStatus, KnowledgeBase
from app.rag.generation import GenerationProviderError
from app.rag.grounding import RenderedAnswer
from app.rag.retrieval import retrieve_evidence
from app.rag.retrieval_types import Evidence, RetrievalMetrics
from app.rag.service import INSUFFICIENT_EVIDENCE, generate_grounded_answer

logger = logging.getLogger(__name__)

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{3,79}$")
_TERMINAL_DOCUMENT_STATUSES = {DocumentStatus.READY, DocumentStatus.FAILED}


class EvaluationRuntimeError(RuntimeError):
    def __init__(
        self,
        safe_message: str,
        *,
        documents: tuple[EvaluationDocumentRecord, ...] = (),
    ) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.documents = documents


class EvaluationExecutionError(RuntimeError):
    def __init__(self, result: EvaluationExecutionResult) -> None:
        super().__init__(result.run.safe_error or "Evaluation failed")
        self.result = result


class EvaluationRuntime(Protocol):
    def public_configuration(self, *, include_answers: bool) -> dict[str, object]: ...

    async def create_workspace(
        self,
        dataset: ExecutableEvaluationDataset,
        run_id: str,
    ) -> EvaluationWorkspace: ...

    async def wait_for_documents(
        self,
        workspace: EvaluationWorkspace,
        dataset: ExecutableEvaluationDataset,
        *,
        timeout_seconds: int,
        poll_seconds: float,
    ) -> tuple[EvaluationDocumentRecord, ...]: ...

    async def run_case(
        self,
        workspace: EvaluationWorkspace,
        case: ExecutableEvaluationCase,
        *,
        include_answers: bool,
    ) -> EvaluationCaseRecord: ...

    async def cleanup(self, workspace: EvaluationWorkspace) -> None: ...


class DatabaseEvaluationRuntime:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        sessions: async_sessionmaker[AsyncSession] = session_factory,
        enqueuer: Callable[[uuid.UUID], bool] = enqueue_ingestion,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings or get_settings()
        self.sessions = sessions
        self.enqueuer = enqueuer
        self.sleeper = sleeper

    def public_configuration(self, *, include_answers: bool) -> dict[str, object]:
        return {
            "chunking_version": self.settings.chunking_version,
            "chunk_tokenizer": self.settings.chunk_tokenizer,
            "hierarchical_retrieval_enabled": (
                self.settings.hierarchical_retrieval_enabled
            ),
            "embedding_model_id": self.settings.embedding_model_id,
            "generation_model_id": (
                self.settings.generation_model_id if include_answers else None
            ),
            "retrieval_semantic_candidates": (
                self.settings.retrieval_semantic_candidates
            ),
            "retrieval_lexical_candidates": self.settings.retrieval_lexical_candidates,
            "retrieval_evidence_limit": self.settings.retrieval_evidence_limit,
            "retrieval_evidence_token_budget": (
                self.settings.retrieval_evidence_token_budget
            ),
        }

    async def create_workspace(
        self,
        dataset: ExecutableEvaluationDataset,
        run_id: str,
    ) -> EvaluationWorkspace:
        name = _knowledge_base_name(dataset.version, run_id)
        knowledge_base = KnowledgeBase(
            name=name,
            description=(
                f"Isolated executable evaluation corpus {dataset.version}; run {run_id}."
            ),
        )
        async with self.sessions() as session:
            session.add(knowledge_base)
            await session.commit()
            await session.refresh(knowledge_base)

        workspace = EvaluationWorkspace(
            knowledge_base_id=knowledge_base.id,
            knowledge_base_name=name,
            document_ids={},
            storage_keys=(),
        )
        storage_keys: list[str] = []
        document_ids: dict[str, uuid.UUID] = {}
        try:
            async with self.sessions() as session:
                for corpus_document in dataset.documents:
                    storage_key = (
                        f"evaluation/{run_id}/{uuid.uuid4().hex}"
                    )
                    destination = storage_path(self.settings.upload_dir, storage_key)
                    await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
                    await asyncio.to_thread(
                        shutil.copyfile,
                        corpus_document.path,
                        destination,
                    )
                    storage_keys.append(storage_key)
                    document = Document(
                        knowledge_base_id=knowledge_base.id,
                        original_name=corpus_document.original_name,
                        storage_key=storage_key,
                        sha256=corpus_document.sha256,
                        media_type=corpus_document.media_type,
                        size_bytes=corpus_document.size_bytes,
                        status=DocumentStatus.PENDING,
                        source_kind=corpus_document.source_kind,
                        language=corpus_document.language,
                        tags=["evaluation", dataset.version],
                        source_path=(
                            f"evaluation/{dataset.version}/"
                            f"{corpus_document.relative_path}"
                        ),
                        source_metadata={
                            "passage_id": corpus_document.passage_id,
                            "locator_id": corpus_document.locator_id,
                            "version": dataset.version,
                        },
                        ingestion_stage="queued",
                        ingestion_progress=0,
                        ingestion_attempts=0,
                        ingestion_warnings=[],
                    )
                    session.add(document)
                    await session.flush()
                    document_ids[corpus_document.document_id] = document.id
                await session.commit()

            workspace = EvaluationWorkspace(
                knowledge_base_id=knowledge_base.id,
                knowledge_base_name=name,
                document_ids=document_ids,
                storage_keys=tuple(storage_keys),
            )
            for document_id in document_ids.values():
                if not self.enqueuer(document_id):
                    raise EvaluationRuntimeError(
                        "The evaluation Document could not be queued; verify Redis and the worker"
                    )
            return workspace
        except Exception:
            await self.cleanup(
                EvaluationWorkspace(
                    knowledge_base_id=knowledge_base.id,
                    knowledge_base_name=name,
                    document_ids=document_ids,
                    storage_keys=tuple(storage_keys),
                )
            )
            raise

    async def wait_for_documents(
        self,
        workspace: EvaluationWorkspace,
        dataset: ExecutableEvaluationDataset,
        *,
        timeout_seconds: int,
        poll_seconds: float,
    ) -> tuple[EvaluationDocumentRecord, ...]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            documents = await self._load_documents(workspace)
            if len(documents) != len(dataset.documents):
                raise EvaluationRuntimeError(
                    "The isolated evaluation Knowledge Base lost a Document"
                )
            if all(item.status in _TERMINAL_DOCUMENT_STATUSES for item in documents):
                records = await self._document_records(workspace, dataset, documents)
                failures = [item for item in records if item.status == DocumentStatus.FAILED]
                if failures:
                    names = ", ".join(item.original_name for item in failures)
                    raise EvaluationRuntimeError(
                        f"Evaluation Ingestion failed for: {names}",
                        documents=records,
                    )
                return records
            if asyncio.get_running_loop().time() >= deadline:
                records = await self._document_records(workspace, dataset, documents)
                raise EvaluationRuntimeError(
                    "Evaluation Documents did not reach terminal states before the timeout",
                    documents=records,
                )
            await self.sleeper(poll_seconds)

    async def run_case(
        self,
        workspace: EvaluationWorkspace,
        case: ExecutableEvaluationCase,
        *,
        include_answers: bool,
    ) -> EvaluationCaseRecord:
        case_started = perf_counter()
        vector = (await embed_texts([case.query], settings=self.settings))[0]
        metrics = RetrievalMetrics()
        async with self.sessions() as session:
            evidence, sufficient = await retrieve_evidence(
                session,
                workspace.knowledge_base_id,
                case.query,
                vector,
                self.settings,
                metrics=metrics,
            )
        evidence_records = tuple(_evidence_record(item) for item in evidence)
        passage_ids = _ordered_unique(item.passage_id for item in evidence_records)
        citation_pages = _citation_pages(evidence)
        retrieval_passed = (
            sufficient and set(case.required_passage_ids) <= set(passage_ids)
            if case.expects_supported_answer
            else not sufficient
        )
        answer: EvaluationAnswerRecord | None = None
        answer_faithful = True
        if include_answers:
            try:
                if sufficient:
                    rendered, usage = await generate_grounded_answer(
                        case.query,
                        [],
                        evidence,
                    )
                else:
                    rendered = RenderedAnswer(
                        content=INSUFFICIENT_EVIDENCE,
                        used_evidence=[],
                        outcome="insufficient_evidence",
                    )
                    usage = {}
            except GenerationProviderError as error:
                logger.warning(
                    "Grounded answer generation failed for evaluation case %s: %s",
                    case.case_id,
                    error.safe_message,
                )
                answer_faithful = False
                answer = EvaluationAnswerRecord(
                    content="",
                    outcome="generation_error",
                    used_passage_ids=(),
                    expected_terms_found=False,
                    usage={},
                    safe_error=error.safe_message,
                )
                citation_pages = ()
            else:
                used_passages = _ordered_unique(
                    _passage_id(item) for item in rendered.used_evidence
                )
                expected_terms_found = all(
                    term.casefold() in rendered.content.casefold()
                    for term in case.expected_answer_terms
                )
                if case.expects_supported_answer:
                    answer_faithful = (
                        rendered.outcome == "grounded_answer"
                        and expected_terms_found
                        and set(case.required_passage_ids) <= set(used_passages)
                    )
                else:
                    answer_faithful = (
                        rendered.outcome == "insufficient_evidence" and not used_passages
                    )
                answer = EvaluationAnswerRecord(
                    content=rendered.content,
                    outcome=rendered.outcome,
                    used_passage_ids=used_passages,
                    expected_terms_found=expected_terms_found,
                    usage=dict(usage),
                )
                citation_pages = _citation_pages(rendered.used_evidence)
        return EvaluationCaseRecord(
            case_id=case.case_id,
            category=case.category,
            query=case.query,
            expects_supported_answer=case.expects_supported_answer,
            relevant_passage_ids=case.relevant_passage_ids,
            required_passage_ids=case.required_passage_ids,
            expected_citation_pages=case.expected_citation_pages,
            retrieved_passage_ids=passage_ids,
            reranked_passage_ids=passage_ids,
            citation_pages=citation_pages,
            sufficient=sufficient,
            retrieval_passed=retrieval_passed,
            answer_faithful=answer_faithful,
            retrieval_latency_ms=metrics.retrieval_duration_ms,
            end_to_end_latency_ms=(perf_counter() - case_started) * 1_000,
            evidence_tokens=metrics.evidence_token_count,
            used_summary_path=False,
            retrieval_metrics=asdict(metrics),
            evidence=evidence_records,
            answer=answer,
        )

    async def cleanup(self, workspace: EvaluationWorkspace) -> None:
        async with self.sessions() as session:
            knowledge_base = await session.get(KnowledgeBase, workspace.knowledge_base_id)
            if knowledge_base is not None:
                await session.delete(knowledge_base)
                await session.commit()
        for storage_key in workspace.storage_keys:
            await asyncio.to_thread(
                remove_stored_file_sync,
                self.settings.upload_dir,
                storage_key,
            )

    async def _load_documents(
        self,
        workspace: EvaluationWorkspace,
    ) -> list[Document]:
        async with self.sessions() as session:
            return list(
                await session.scalars(
                    select(Document).where(
                        Document.id.in_(tuple(workspace.document_ids.values()))
                    )
                )
            )

    async def _document_records(
        self,
        workspace: EvaluationWorkspace,
        dataset: ExecutableEvaluationDataset,
        documents: list[Document],
    ) -> tuple[EvaluationDocumentRecord, ...]:
        by_id = {item.id: item for item in documents}
        records: list[EvaluationDocumentRecord] = []
        async with self.sessions() as session:
            for corpus_document in dataset.documents:
                document_id = workspace.document_ids[corpus_document.document_id]
                document = by_id[document_id]
                chunk_count = await session.scalar(
                    select(func.count())
                    .select_from(Chunk)
                    .where(
                        Chunk.document_id == document_id,
                        Chunk.chunk_level == ChunkLevel.CHILD,
                    )
                )
                records.append(
                    EvaluationDocumentRecord(
                        corpus_document_id=corpus_document.document_id,
                        document_id=str(document.id),
                        original_name=document.original_name,
                        status=str(document.status),
                        ingestion_stage=document.ingestion_stage,
                        ingestion_progress=document.ingestion_progress,
                        ingestion_attempts=document.ingestion_attempts,
                        parser_version=document.parser_version,
                        chunking_version=document.chunking_version,
                        chunk_count=int(chunk_count or 0),
                        warnings=tuple(document.ingestion_warnings or []),
                        safe_error=document.safe_error,
                    )
                )
        return tuple(records)


async def execute_evaluation(
    options: EvaluationRunOptions,
    *,
    runtime: EvaluationRuntime | None = None,
) -> EvaluationExecutionResult:
    _validate_options(options)
    dataset = load_executable_dataset(options.dataset_path)
    active_runtime = runtime or DatabaseEvaluationRuntime()
    run_id = options.run_id or _new_run_id()
    started_at = datetime.now(UTC)
    workspace: EvaluationWorkspace | None = None
    documents: tuple[EvaluationDocumentRecord, ...] = ()
    cases: list[EvaluationCaseRecord] = []
    safe_error: str | None = None
    cleaned_up = False
    caught_error: Exception | None = None

    try:
        workspace = await active_runtime.create_workspace(dataset, run_id)
        documents = await active_runtime.wait_for_documents(
            workspace,
            dataset,
            timeout_seconds=options.ingestion_timeout_seconds,
            poll_seconds=options.poll_seconds,
        )
        for case in dataset.cases:
            cases.append(
                await active_runtime.run_case(
                    workspace,
                    case,
                    include_answers=options.include_answers,
                )
            )
        answer_failures = [
            item.case_id
            for item in cases
            if item.answer is not None and item.answer.safe_error is not None
        ]
        if answer_failures:
            joined = ", ".join(answer_failures)
            raise EvaluationRuntimeError(
                f"Grounded answer generation failed for {len(answer_failures)} "
                f"evaluation case(s): {joined}"
            )
    except EvaluationRuntimeError as error:
        caught_error = error
        safe_error = error.safe_message
        if error.documents:
            documents = error.documents
    except Exception as error:
        logger.exception("Unexpected executable evaluation failure")
        caught_error = error
        safe_error = "Evaluation failed unexpectedly; inspect the runner logs"
    finally:
        if workspace is not None and not options.keep_knowledge_base:
            try:
                await active_runtime.cleanup(workspace)
                cleaned_up = True
            except Exception as cleanup_error:
                logger.exception("Could not clean up the evaluation Knowledge Base")
                caught_error = caught_error or cleanup_error
                cleanup_message = "The isolated evaluation Knowledge Base could not be cleaned up"
                safe_error = (
                    f"{safe_error}; {cleanup_message}" if safe_error else cleanup_message
                )

    run = EvaluationRunRecord(
        schema_version=1,
        run_id=run_id,
        status="failed" if caught_error is not None else "completed",
        evaluation_set_version=dataset.version,
        started_at=started_at.isoformat(),
        completed_at=datetime.now(UTC).isoformat(),
        knowledge_base_id=(str(workspace.knowledge_base_id) if workspace else None),
        knowledge_base_name=(workspace.knowledge_base_name if workspace else None),
        knowledge_base_cleaned_up=cleaned_up,
        include_answers=options.include_answers,
        configuration=active_runtime.public_configuration(
            include_answers=options.include_answers
        ),
        documents=documents,
        cases=tuple(cases),
        safe_error=safe_error,
    )
    aggregate = build_aggregate_report(run)
    artifacts = write_evaluation_artifacts(options.output_dir, run, aggregate)
    result = EvaluationExecutionResult(run=run, aggregate=aggregate, artifacts=artifacts)
    if caught_error is not None:
        raise EvaluationExecutionError(result) from caught_error
    return result


def _evidence_record(evidence: Evidence) -> EvaluationEvidenceRecord:
    return EvaluationEvidenceRecord(
        rank=evidence.retrieval_rank,
        passage_id=_passage_id(evidence),
        locator_id=_locator_id(evidence),
        document_id=str(evidence.document_id),
        document_name=evidence.document_name,
        chunk_id=str(evidence.chunk_id),
        locator=dict(evidence.locator),
        text=evidence.text,
        retrieval_score=evidence.retrieval_score,
        semantic_similarity=evidence.semantic_similarity,
        lexical_score=evidence.lexical_score,
    )


def _passage_id(evidence: Evidence) -> str:
    value = evidence.locator.get("source_passage_id")
    if not isinstance(value, str) or not value:
        raise EvaluationRuntimeError(
            f"Retrieved evaluation chunk {evidence.chunk_id} has no stable passage ID"
        )
    return value


def _locator_id(evidence: Evidence) -> str:
    value = evidence.locator.get("source_locator_id")
    if not isinstance(value, str) or not value:
        raise EvaluationRuntimeError(
            f"Retrieved evaluation chunk {evidence.chunk_id} has no stable locator ID"
        )
    return value


def _citation_pages(evidence: list[Evidence]) -> tuple[int, ...]:
    pages: list[int] = []
    for item in evidence:
        page = item.locator.get("page")
        if isinstance(page, int) and not isinstance(page, bool):
            pages.append(page)
            continue
        start = item.locator.get("start_page")
        end = item.locator.get("end_page")
        if isinstance(start, int) and isinstance(end, int):
            pages.extend(range(start, end + 1))
    return tuple(dict.fromkeys(pages))


def _ordered_unique(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _validate_options(options: EvaluationRunOptions) -> None:
    if options.ingestion_timeout_seconds < 1:
        raise ValueError("ingestion_timeout_seconds must be positive")
    if options.poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    if options.run_id is not None and not _RUN_ID.fullmatch(options.run_id):
        raise ValueError("run_id must contain 4-80 letters, numbers, underscores, or hyphens")


def _knowledge_base_name(version: str, run_id: str) -> str:
    safe_version = re.sub(r"[^A-Za-z0-9_-]+", "-", version).strip("-") or "corpus"
    return f"Evaluation {safe_version[:40]} {run_id}"[:120]


def _new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:10]}"


__all__ = [
    "DatabaseEvaluationRuntime",
    "EvaluationExecutionError",
    "EvaluationRunOptions",
    "EvaluationRuntime",
    "EvaluationRuntimeError",
    "execute_evaluation",
]

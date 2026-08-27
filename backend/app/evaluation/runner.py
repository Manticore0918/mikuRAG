import asyncio
import hashlib
import json
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
    EvaluationCorpusDocument,
    ExecutableEvaluationCase,
    ExecutableEvaluationDataset,
    load_executable_dataset,
    passage_matches_locator,
)
from app.evaluation.reporting import build_aggregate_report, write_evaluation_artifacts
from app.ingestion.chunkers import (
    _CHUNKER_PROFILES,
    build_chunker,
    canonical_profile,
)
from app.ingestion.dispatch import enqueue_ingestion
from app.ingestion.embeddings import embed_texts
from app.ingestion.storage import remove_stored_file_sync, storage_path
from app.models import Chunk, ChunkLevel, Document, DocumentStatus, KnowledgeBase
from app.rag.generation import GenerationProviderError, complete_json
from app.rag.grounding import HistoryMessage, RenderedAnswer
from app.rag.query_plan import build_query_plan
from app.rag.reranking import build_reranker
from app.rag.retrieval import retrieve_evidence
from app.rag.retrieval_types import (
    Evidence,
    QueryPlan,
    RetrievalFilters,
    RetrievalMetrics,
    RetrievalMode,
)
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
        dataset: ExecutableEvaluationDataset,
        *,
        include_answers: bool,
    ) -> EvaluationCaseRecord: ...

    def ingestion_statistics(self) -> dict[str, object]: ...

    async def cleanup(self, workspace: EvaluationWorkspace) -> None: ...


class DatabaseEvaluationRuntime:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        sessions: async_sessionmaker[AsyncSession] = session_factory,
        enqueuer: Callable[[uuid.UUID, str | None], bool] = enqueue_ingestion,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        target_chunking_version: str | None = None,
        retrieval_mode: str | None = None,
        reranker_provider: str | None = None,
        bm25_hybrid_enabled: bool | None = None,
        query_planning: bool = True,
    ) -> None:
        self.settings = settings or get_settings()
        self.sessions = sessions
        self.enqueuer = enqueuer
        self.sleeper = sleeper
        self.target_chunking_version = target_chunking_version
        self.retrieval_mode = (
            RetrievalMode(retrieval_mode)
            if retrieval_mode is not None
            else self.settings.retrieval_mode
        )
        self.reranker = build_reranker(reranker_provider, self.settings)
        self.effective_settings = (
            self.settings.model_copy(update={"bm25_hybrid_enabled": bm25_hybrid_enabled})
            if bm25_hybrid_enabled is not None
            else self.settings
        )
        self.query_planning = query_planning
        self._ingestion_started = 0.0
        self._ingestion_duration_ms: float | None = None
        self._embedding_input_count = 0
        self._total_chunk_count = 0
        self._storage_estimate_bytes = 0

    def public_configuration(self, *, include_answers: bool) -> dict[str, object]:
        settings = self.effective_settings
        profile = self.target_chunking_version or settings.chunking_version
        chunker = build_chunker(settings, version=profile)
        return {
            "chunking_version": profile,
            "chunking_config_hash": chunker.config.config_hash,
            "chunk_tokenizer": settings.chunk_tokenizer,
            "hierarchical_retrieval_enabled": (settings.hierarchical_retrieval_enabled),
            "embedding_model_id": settings.embedding_model_id,
            "generation_model_id": (settings.generation_model_id if include_answers else None),
            "retrieval_mode": self.retrieval_mode.value,
            "retrieval_rrf_k": settings.retrieval_rrf_k,
            "retrieval_rrf_semantic_weight": settings.retrieval_rrf_semantic_weight,
            "retrieval_rrf_lexical_weight": settings.retrieval_rrf_lexical_weight,
            "reranker_provider": getattr(self.reranker, "provider_name", "deterministic"),
            "bm25_hybrid_enabled": settings.bm25_hybrid_enabled,
            "query_planning": self.query_planning,
            "retrieval_semantic_candidates": (settings.retrieval_semantic_candidates),
            "retrieval_lexical_candidates": settings.retrieval_lexical_candidates,
            "retrieval_rerank_candidates": settings.retrieval_rerank_candidates,
            "retrieval_evidence_limit": settings.retrieval_evidence_limit,
            "retrieval_evidence_token_budget": (settings.retrieval_evidence_token_budget),
        }

    async def create_workspace(
        self,
        dataset: ExecutableEvaluationDataset,
        run_id: str,
    ) -> EvaluationWorkspace:
        name = _knowledge_base_name(dataset.version, run_id)
        knowledge_base = KnowledgeBase(
            name=name,
            description=(f"Isolated executable evaluation corpus {dataset.version}; run {run_id}."),
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
        self._storage_estimate_bytes = sum(item.size_bytes for item in dataset.documents)
        storage_keys: list[str] = []
        document_ids: dict[str, uuid.UUID] = {}
        try:
            async with self.sessions() as session:
                for corpus_document in dataset.documents:
                    storage_key = f"evaluation/{run_id}/{uuid.uuid4().hex}"
                    destination = storage_path(self.settings.upload_dir, storage_key)
                    await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
                    await asyncio.to_thread(
                        shutil.copyfile,
                        corpus_document.path,
                        destination,
                    )
                    storage_keys.append(storage_key)
                    source_metadata: dict[str, object] = {"version": dataset.version}
                    if (
                        len(corpus_document.passages) == 1
                        and not corpus_document.passages[0].locator_match
                    ):
                        source_metadata.update(
                            {
                                "passage_id": corpus_document.passages[0].passage_id,
                                "locator_id": corpus_document.passages[0].locator_id,
                            }
                        )
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
                        tags=["evaluation", dataset.version, *corpus_document.tags],
                        source_uri=corpus_document.source_uri,
                        source_path=(
                            f"evaluation/{dataset.version}/{corpus_document.relative_path}"
                        ),
                        source_metadata=source_metadata,
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
            self._ingestion_started = perf_counter()
            for document_id in document_ids.values():
                if not self.enqueuer(document_id, self.target_chunking_version):
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
                await self._bind_gold_passages(workspace, dataset)
                self._ingestion_duration_ms = (perf_counter() - self._ingestion_started) * 1_000
                self._total_chunk_count = sum(item.chunk_count for item in records)
                self._embedding_input_count = await self._count_embedding_inputs(workspace)
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
        dataset: ExecutableEvaluationDataset,
        *,
        include_answers: bool,
    ) -> EvaluationCaseRecord:
        case_started = perf_counter()
        metrics = RetrievalMetrics()
        query_plan: QueryPlan | None = None
        history = [
            HistoryMessage(role=item.role, content=item.content)
            for item in case.history
        ]
        if self.query_planning:
            plan, _ = await build_query_plan(
                case.query,
                history,
                self.effective_settings,
                complete=complete_json,
                metrics=metrics,
            )
            query_plan = plan
        query = query_plan.effective_query if query_plan is not None else case.query
        vector = (await embed_texts([query], settings=self.effective_settings))[0]
        retrieval_filters = _retrieval_filters_for_case(case, workspace)
        async with self.sessions() as session:
            evidence, sufficient = await retrieve_evidence(
                session,
                workspace.knowledge_base_id,
                query,
                vector,
                self.effective_settings,
                mode=self.retrieval_mode,
                reranker=self.reranker,
                filters=retrieval_filters,
                query_plan=query_plan,
                metrics=metrics,
            )
        evidence_records = tuple(_evidence_record(item) for item in evidence)
        filter_correct = _filter_correct(dataset, workspace, case, evidence_records)
        passage_ids = _ordered_unique(item.passage_id for item in evidence_records)
        citation_pages = _citation_pages(evidence)
        retrieval_passed = (
            sufficient and set(case.required_passage_ids) <= set(passage_ids)
            if case.expects_supported_answer
            # Conflicting-evidence cases (non-supported with required passages)
            # pass retrieval only when the retriever surfaced both sides of the
            # conflict; sufficient is expected here because both are strong hits.
            else (
                set(case.required_passage_ids) <= set(passage_ids)
                if case.required_passage_ids
                else not sufficient
            )
        )
        answer: EvaluationAnswerRecord | None = None
        answer_faithful = True
        if include_answers:
            try:
                if sufficient:
                    rendered, usage = await generate_grounded_answer(
                        case.query,
                        history,
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
                answer_faithful = _answer_is_faithful(
                    expects_supported_answer=case.expects_supported_answer,
                    required_passage_ids=case.required_passage_ids,
                    expected_terms_found=expected_terms_found,
                    outcome=rendered.outcome,
                    content=rendered.content,
                    used_passages=used_passages,
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
            filters=case.filters,
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
            split=case.split,
            relevance_grades=dict(case.relevance_grades),
            filter_correct=filter_correct,
            effective_query=query,
            history=tuple(
                {"role": item.role, "content": item.content}
                for item in case.history
            ),
            rewrite_status=(query_plan.status.value if query_plan is not None else None),
            preserved_identifiers=(
                query_plan.preserved_identifiers if query_plan is not None else ()
            ),
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
                    select(Document).where(Document.id.in_(tuple(workspace.document_ids.values())))
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
                chunking_config_hash = await session.scalar(
                    select(func.max(Chunk.chunking_config_hash)).where(
                        Chunk.document_id == document_id
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
                        size_bytes=document.size_bytes or 0,
                        chunking_config_hash=(
                            str(chunking_config_hash)
                            if isinstance(chunking_config_hash, str) and chunking_config_hash
                            else None
                        ),
                    )
                )
        return tuple(records)

    async def _count_embedding_inputs(
        self,
        workspace: EvaluationWorkspace,
    ) -> int:
        """Count chunks that were embedded during ingestion.

        Every chunk level that received an embedding represents one embedding
        model input during indexing (children, plus section/document summaries
        for the hierarchical profile).
        """
        async with self.sessions() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(Chunk)
                    .where(
                        Chunk.document_id.in_(tuple(workspace.document_ids.values())),
                        Chunk.embedding_model.is_not(None),
                    )
                )
                or 0
            )

    def ingestion_statistics(self) -> dict[str, object]:
        return {
            "ingestion_duration_ms": self._ingestion_duration_ms,
            "embedding_input_count": self._embedding_input_count,
            "total_chunk_count": self._total_chunk_count,
            "storage_estimate_bytes": self._storage_estimate_bytes,
        }

    async def _bind_gold_passages(
        self,
        workspace: EvaluationWorkspace,
        dataset: ExecutableEvaluationDataset,
    ) -> None:
        async with self.sessions() as session:
            for corpus_document in dataset.documents:
                if (
                    len(corpus_document.passages) == 1
                    and not corpus_document.passages[0].locator_match
                ):
                    continue
                document_id = workspace.document_ids[corpus_document.document_id]
                chunks = list(
                    await session.scalars(
                        select(Chunk).where(
                            Chunk.document_id == document_id,
                            Chunk.chunk_level == ChunkLevel.CHILD,
                        )
                    )
                )
                matched_passages: set[str] = set()
                for chunk in chunks:
                    locator = dict(chunk.locator)
                    matches = [
                        passage
                        for passage in corpus_document.passages
                        if passage_matches_locator(passage, locator)
                    ]
                    if len(matches) > 1:
                        raise EvaluationRuntimeError(
                            f"Evaluation Document '{corpus_document.document_id}' has "
                            "an ambiguous gold locator mapping"
                        )
                    if matches:
                        passage = matches[0]
                        matched_passages.add(passage.passage_id)
                        locator["source_passage_id"] = passage.passage_id
                        locator["source_locator_id"] = passage.locator_id
                    else:
                        passage_id, locator_id = _unjudged_ids(corpus_document, chunk)
                        locator["source_passage_id"] = passage_id
                        locator["source_locator_id"] = locator_id
                    chunk.locator = locator
                missing = {
                    passage.passage_id for passage in corpus_document.passages
                } - matched_passages
                if missing:
                    raise EvaluationRuntimeError(
                        f"Evaluation Document '{corpus_document.document_id}' did not "
                        f"produce chunks for gold passages: {', '.join(sorted(missing))}"
                    )
            await session.commit()


async def execute_evaluation(
    options: EvaluationRunOptions,
    *,
    runtime: EvaluationRuntime | None = None,
    dataset: ExecutableEvaluationDataset | None = None,
) -> EvaluationExecutionResult:
    _validate_options(options)
    dataset = dataset or load_executable_dataset(options.dataset_path)
    active_runtime = runtime or DatabaseEvaluationRuntime(
        target_chunking_version=options.target_chunking_version,
        retrieval_mode=options.retrieval_mode,
        reranker_provider=options.reranker_provider,
        bm25_hybrid_enabled=options.bm25_hybrid_enabled,
        query_planning=options.query_planning,
    )
    run_id = options.run_id or _new_run_id()
    started_at = datetime.now(UTC)
    workspace: EvaluationWorkspace | None = None
    documents: tuple[EvaluationDocumentRecord, ...] = ()
    cases: list[EvaluationCaseRecord] = []
    safe_error: str | None = None
    cleaned_up = False
    caught_error: Exception | None = None
    ingestion_stats: dict[str, object] = {}

    try:
        workspace = await active_runtime.create_workspace(dataset, run_id)
        documents = await active_runtime.wait_for_documents(
            workspace,
            dataset,
            timeout_seconds=options.ingestion_timeout_seconds,
            poll_seconds=options.poll_seconds,
        )
        ingestion_stats = active_runtime.ingestion_statistics()
        for case in dataset.cases:
            cases.append(
                await active_runtime.run_case(
                    workspace,
                    case,
                    dataset,
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
                safe_error = f"{safe_error}; {cleanup_message}" if safe_error else cleanup_message

    configuration = dict(
        active_runtime.public_configuration(include_answers=options.include_answers)
    )
    configuration["bootstrap_samples"] = options.bootstrap_samples
    configuration["bootstrap_seed"] = options.bootstrap_seed
    raw_config_hash = configuration.get("chunking_config_hash")
    chunking_config_hash = str(raw_config_hash) if isinstance(raw_config_hash, str) else None
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
        configuration=configuration,
        documents=documents,
        cases=tuple(cases),
        safe_error=safe_error,
        chunking_config_hash=chunking_config_hash,
        ingestion_duration_ms=(
            float(ingestion_stats["ingestion_duration_ms"])
            if ingestion_stats.get("ingestion_duration_ms") is not None
            else None
        ),
        embedding_input_count=int(ingestion_stats.get("embedding_input_count") or 0),
        total_chunk_count=int(ingestion_stats.get("total_chunk_count") or 0),
        storage_estimate_bytes=int(ingestion_stats.get("storage_estimate_bytes") or 0),
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


def _unjudged_ids(
    document: EvaluationCorpusDocument,
    chunk: Chunk,
) -> tuple[str, str]:
    locator_text = json.dumps(chunk.locator, sort_keys=True, separators=(",", ":"))
    locator_hash = hashlib.sha256(locator_text.encode("utf-8")).hexdigest()[:16]
    content_key = (chunk.content_hash or hashlib.sha256(chunk.text.encode()).hexdigest())[:16]
    return (
        f"unjudged:{document.document_id}:{content_key}",
        f"unjudged:{document.document_id}:{locator_hash}",
    )


def _ordered_unique(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _filter_correct(
    dataset: ExecutableEvaluationDataset,
    workspace: EvaluationWorkspace,
    case: ExecutableEvaluationCase,
    evidence_records: tuple[EvaluationEvidenceRecord, ...],
) -> bool | None:
    """Whether every retrieved piece of evidence satisfied the case filters.

    Returns ``None`` when the case carries no metadata filters. Evidence whose
    source Document is outside the corpus, or that violates a filter, fails the
    check. An empty evidence list vacuously respects the filters.
    """
    if not case.filters:
        return None
    db_id_to_corpus_id = {
        str(document_id): corpus_id for corpus_id, document_id in workspace.document_ids.items()
    }
    corpus_documents = {item.document_id: item for item in dataset.documents}
    for record in evidence_records:
        corpus_id = db_id_to_corpus_id.get(record.document_id)
        document = corpus_documents.get(corpus_id) if corpus_id else None
        if document is None or not _document_satisfies_filters(document, case.filters):
            return False
    return True


def _retrieval_filters_for_case(
    case: ExecutableEvaluationCase,
    workspace: EvaluationWorkspace,
) -> RetrievalFilters:
    """Resolve corpus-level filters to the database identifiers used by retrieval.

    Evaluation manifests intentionally use stable corpus Document IDs. Each isolated
    run receives fresh database UUIDs, so Document filters must be translated through
    the run workspace before the production retriever is called.
    """
    document_ids = tuple(
        workspace.document_ids[corpus_id]
        for corpus_id in case.filters.get("document_ids", ())
    )
    return RetrievalFilters(
        document_ids=document_ids,
        tags=case.filters.get("tags", ()),
        source_kinds=case.filters.get("source_kinds", ()),
        languages=case.filters.get("languages", ()),
    )


def _document_satisfies_filters(
    document: EvaluationCorpusDocument,
    filters: dict[str, tuple[str, ...]],
) -> bool:
    if "document_ids" in filters and document.document_id not in filters["document_ids"]:
        return False
    if "tags" in filters and not set(filters["tags"]) <= set(document.tags):
        return False
    if "source_kinds" in filters and document.source_kind not in filters["source_kinds"]:
        return False
    if "languages" in filters and document.language not in filters["languages"]:
        return False
    return True


_CONFLICT_PREFIX = "i cannot answer reliably because the retrieved documents conflict"


def _answer_is_faithful(
    *,
    expects_supported_answer: bool,
    required_passage_ids: tuple[str, ...],
    expected_terms_found: bool,
    outcome: str,
    content: str,
    used_passages: tuple[str, ...],
) -> bool:
    """Score answer faithfulness for one evaluation case.

    Supported cases must produce a grounded answer that contains every expected
    term and cites every required passage. Unsupported cases (no required
    passages) are faithful only when they refuse with an insufficiency statement
    and cite nothing. Conflicting-evidence cases (unsupported but with required
    passages) are faithful only when they refuse by naming both sides of the
    conflict and cite both required passages.
    """
    if expects_supported_answer:
        return (
            outcome == "grounded_answer"
            and expected_terms_found
            and set(required_passage_ids) <= set(used_passages)
        )
    if required_passage_ids:
        return (
            outcome == "conflicting_evidence"
            and content.casefold().startswith(_CONFLICT_PREFIX)
            and set(required_passage_ids) <= set(used_passages)
        )
    return outcome == "insufficient_evidence" and not used_passages


def _validate_options(options: EvaluationRunOptions) -> None:
    if options.ingestion_timeout_seconds < 1:
        raise ValueError("ingestion_timeout_seconds must be positive")
    if options.poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    if options.run_id is not None and not _RUN_ID.fullmatch(options.run_id):
        raise ValueError("run_id must contain 4-80 letters, numbers, underscores, or hyphens")
    if (
        options.target_chunking_version is not None
        and canonical_profile(options.target_chunking_version) not in _CHUNKER_PROFILES
    ):
        raise ValueError(f"Unsupported chunking profile: {options.target_chunking_version}")
    if options.retrieval_mode is not None:
        try:
            RetrievalMode(options.retrieval_mode)
        except ValueError as error:
            raise ValueError(f"Unsupported retrieval mode: {options.retrieval_mode}") from error
    if options.reranker_provider is not None and options.reranker_provider not in {
        "deterministic",
        "cross_encoder",
    }:
        raise ValueError(f"Unsupported reranker provider: {options.reranker_provider}")


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

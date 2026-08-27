import asyncio
import logging
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Literal

from celery.exceptions import CeleryError
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.celery_app import celery_app
from app.config import Settings, get_settings
from app.ingestion.chunkers import build_chunker, is_hierarchical_chunker
from app.ingestion.embeddings import EmbeddingMetrics, embed_texts
from app.ingestion.errors import EmbeddingProviderError, IngestionError
from app.ingestion.extraction import extract_document, parser_version_for_media_type
from app.ingestion.hierarchical_chunking import HierarchicalChunkingConfig
from app.ingestion.normalization import normalize_document
from app.ingestion.persistence import (
    build_hierarchical_chunk_models,
    build_legacy_chunk_models,
    build_summary_chunk_models,
    replace_document_chunks,
)
from app.ingestion.provenance import chunk_provenance
from app.ingestion.storage import remove_stored_file_sync, storage_path
from app.ingestion.summarization import (
    SummaryGenerationError,
    generate_hierarchical_summaries,
)
from app.ingestion.tokenization import create_tokenizer
from app.ingestion.validation import validate_document_limits, validate_hierarchy
from app.models import Document, DocumentStatus
from app.observability import (
    emit_observation,
    rounded_percentage,
    token_distribution,
    warning_page_count,
)
from app.rag.generation import GenerationProviderError

logger = logging.getLogger(__name__)


async def run_ingestion(
    document_id: uuid.UUID,
    target_chunking_version: str | None = None,
) -> Literal["busy", "completed", "failed", "skipped"]:
    ingestion_started = perf_counter()
    settings = get_settings()
    chunking_version = target_chunking_version or settings.chunking_version
    if chunking_version not in {
        "legacy",
        "legacy_char_v1",
        "token_recursive_v1",
        "hierarchical_v1",
    }:
        raise ValueError("Unsupported chunking version")
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    stage = "claim"
    outcome = "skipped"
    embedding_metrics = EmbeddingMetrics()
    observation = {
        "document_id": str(document_id),
        "chunking_version": chunking_version,
        "chunking_config_hash": "",
        "extracted_block_count": 0,
        "empty_page_count": 0,
        "ocr_fallback_page_count": 0,
        "parent_count": 0,
        "child_count": 0,
        "summary_count": 0,
        "child_token_distribution": {"count": 0},
        "cross_page_child_percentage": 0.0,
        "extraction_duration_ms": 0.0,
        "normalization_duration_ms": 0.0,
        "chunk_construction_duration_ms": 0.0,
        "validation_duration_ms": 0.0,
        "persistence_duration_ms": 0.0,
    }
    try:
        async with sessions() as session:
            stale_before = datetime.now(UTC) - timedelta(
                seconds=settings.ingestion_stale_after_seconds
            )
            claimed = await session.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    or_(
                        Document.status.in_(
                            [DocumentStatus.PENDING, DocumentStatus.FAILED]
                        ),
                        and_(
                            Document.status == DocumentStatus.PROCESSING,
                            Document.updated_at <= stale_before,
                        ),
                    ),
                )
                .values(
                    status=DocumentStatus.PROCESSING,
                    safe_error=None,
                    ingestion_stage="extract",
                    ingestion_progress=5,
                    ingestion_attempts=Document.ingestion_attempts + 1,
                    updated_at=func.now(),
                )
            )
            await session.commit()
            if claimed.rowcount != 1:
                current_status = await session.scalar(
                    select(Document.status).where(Document.id == document_id)
                )
                return (
                    "busy"
                    if current_status == DocumentStatus.PROCESSING
                    else "skipped"
                )
            outcome = "failed"

        async with sessions() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return "skipped"
            path = storage_path(settings.upload_dir, document.storage_key)
            media_type = document.media_type
            parser_version = parser_version_for_media_type(media_type)
            document.parser_version = parser_version
            await session.commit()

        stage = "extract"
        stage_started = perf_counter()
        extracted = await asyncio.to_thread(
            extract_document,
            path,
            media_type,
            settings.max_document_pages,
            source_kind=document.source_kind,
            source_path=document.source_path,
            source_uri=document.source_uri,
            language=document.language,
        )
        observation["extraction_duration_ms"] = _duration_ms(stage_started)
        observation["extracted_block_count"] = len(extracted.blocks)
        _apply_extracted_provenance(document, extracted)
        stage = "normalize"
        await _update_progress(
            sessions,
            document_id,
            stage=stage,
            progress=25,
            parser_version=parser_version,
            source_uri=document.source_uri,
            source_metadata=document.source_metadata,
        )
        stage_started = perf_counter()
        normalized = await asyncio.to_thread(normalize_document, extracted)
        observation["normalization_duration_ms"] = _duration_ms(stage_started)
        observation["empty_page_count"] = warning_page_count(
            normalized.warnings, "empty_page"
        )
        observation["ocr_fallback_page_count"] = warning_page_count(
            normalized.warnings, "ocr_fallback_used"
        )
        serialized_warnings = [asdict(warning) for warning in normalized.warnings[:100]]
        await _update_progress(
            sessions,
            document_id,
            stage="construct",
            progress=40,
            warnings=serialized_warnings,
        )
        for warning in normalized.warnings:
            logger.warning(
                "Document extraction warning for %s: code=%s page=%s",
                document_id,
                warning.code,
                warning.page_number,
            )
        tokenizer = create_tokenizer(settings.chunk_tokenizer)
        provenance = chunk_provenance(document)

        stage = "construct"
        chunker = build_chunker(
            settings, version=chunking_version, tokenizer=tokenizer
        )
        chunking_config_hash = chunker.config.config_hash
        stage_started = perf_counter()
        chunking_result = await asyncio.to_thread(chunker.chunk, normalized)
        observation["chunk_construction_duration_ms"] = _duration_ms(stage_started)
        observation["chunking_config_hash"] = chunking_config_hash

        if is_hierarchical_chunker(chunker):
            hierarchy = chunking_result
            chunking_config = chunker.hierarchical_config
            stage = "validate"
            await _update_progress(sessions, document_id, stage=stage, progress=55)
            stage_started = perf_counter()
            await asyncio.to_thread(
                validate_hierarchy,
                normalized,
                hierarchy,
                config=chunking_config,
                tokenizer=tokenizer,
                max_document_chunks=settings.max_document_chunks,
                max_document_tokens=settings.max_document_tokens,
            )
            observation["validation_duration_ms"] = _duration_ms(stage_started)
            summaries = []
            if settings.summary_generation_enabled:
                stage = "summarize"
                await _update_progress(sessions, document_id, stage=stage, progress=60)
                try:
                    summaries = await generate_hierarchical_summaries(
                        hierarchy.parents,
                        document_name=document.original_name,
                        settings=settings,
                        tokenizer=tokenizer,
                    )
                except (GenerationProviderError, SummaryGenerationError) as error:
                    logger.warning(
                        "Skipping optional summaries for Document %s: %s",
                        document_id,
                        error,
                    )
                if (
                    len(hierarchy.parents)
                    + len(hierarchy.children)
                    + len(summaries)
                    > settings.max_document_chunks
                ):
                    logger.warning(
                        "Skipping optional summaries for Document %s because the safe "
                        "chunk limit would be exceeded",
                        document_id,
                    )
                    summaries = []
            stage = "embed"
            await _update_progress(sessions, document_id, stage=stage, progress=70)
            vectors = await embed_texts(
                [child.embedding_text or child.text for child in hierarchy.children],
                settings=settings,
                metrics=embedding_metrics,
            )
            batch = build_hierarchical_chunk_models(
                document_id=document_id,
                hierarchy=hierarchy,
                vectors=vectors,
                embedding_model=settings.embedding_model_id,
                provenance=provenance,
                chunking_config_hash=chunking_config_hash,
            )
            if summaries:
                try:
                    summary_vectors = await embed_texts(
                        [summary.text for summary in summaries],
                        settings=settings,
                        metrics=embedding_metrics,
                    )
                    batch = replace(
                        batch,
                        summaries=build_summary_chunk_models(
                            document_id=document_id,
                            parents=batch.parents,
                            summaries=summaries,
                            vectors=summary_vectors,
                            embedding_model=settings.embedding_model_id,
                            chunking_config_hash=chunking_config_hash,
                            provenance=provenance,
                        ),
                    )
                except (EmbeddingProviderError, IngestionError) as error:
                    logger.warning(
                        "Skipping optional summary embeddings for Document %s: %s",
                        document_id,
                        error,
                    )
        else:
            chunks = chunking_result
            if not chunks:
                raise IngestionError("No searchable text could be produced from this Document")
            if len(chunks) > settings.max_document_chunks:
                raise IngestionError(
                    "The extracted text exceeds the safe Ingestion limit for one Document"
                )
            stage = "validate"
            await _update_progress(sessions, document_id, stage=stage, progress=55)
            stage_started = perf_counter()
            await asyncio.to_thread(
                validate_document_limits,
                normalized,
                tokenizer=tokenizer,
                max_document_tokens=settings.max_document_tokens,
            )
            observation["validation_duration_ms"] = _duration_ms(stage_started)
            stage = "embed"
            await _update_progress(sessions, document_id, stage=stage, progress=70)
            vectors = await embed_texts(
                [chunk.text for chunk in chunks],
                settings=settings,
                metrics=embedding_metrics,
            )
            batch = build_legacy_chunk_models(
                document_id=document_id,
                chunks=chunks,
                vectors=vectors,
                embedding_model=settings.embedding_model_id,
                tokenizer=tokenizer,
                provenance=provenance,
                chunking_config_hash=chunking_config_hash,
                chunking_version=chunker.profile,
            )

        child_token_counts = [
            chunk.token_count
            for chunk in batch.children
            if chunk.token_count is not None
        ]
        cross_page_children = sum(
            1
            for chunk in batch.children
            if chunk.start_page is not None
            and chunk.end_page is not None
            and chunk.start_page != chunk.end_page
        )
        observation.update(
            {
                "parent_count": len(batch.parents),
                "child_count": len(batch.children),
                "summary_count": len(batch.summaries),
                "child_token_distribution": token_distribution(child_token_counts),
                "cross_page_child_percentage": rounded_percentage(
                    cross_page_children, len(batch.children)
                ),
            }
        )
        stage = "persist"
        await _update_progress(sessions, document_id, stage=stage, progress=90)
        stage_started = perf_counter()
        async with sessions() as session:
            document = await session.scalar(
                select(Document).where(Document.id == document_id).with_for_update()
            )
            if document is None or document.status != DocumentStatus.PROCESSING:
                return "skipped"
            await replace_document_chunks(
                session,
                document_id=document_id,
                batch=batch,
            )
            document.page_count = normalized.page_count
            document.status = DocumentStatus.READY
            document.safe_error = None
            document.parser_version = normalized.parser_version or parser_version
            document.chunking_version = chunking_version
            document.source_kind = normalized.source_kind or document.source_kind
            document.language = normalized.language or document.language
            document.ingestion_stage = "ready"
            document.ingestion_progress = 100
            document.ingestion_warnings = serialized_warnings
            await session.commit()
            observation["persistence_duration_ms"] = _duration_ms(stage_started)
            outcome = "completed"
    except IngestionError as error:
        safe_error = _stage_error(stage, error.safe_message)
        logger.warning("Document ingestion failed for %s: %s", document_id, safe_error)
        await _mark_failed(sessions, document_id, safe_error)
    except Exception:
        logger.exception(
            "Unexpected Document ingestion failure for %s during %s",
            document_id,
            stage,
        )
        await _mark_failed(
            sessions,
            document_id,
            _stage_error(
                stage,
                "Ingestion failed unexpectedly. Retry the Document or inspect the worker logs.",
            ),
        )
    finally:
        try:
            await engine.dispose()
        finally:
            emit_observation(
                logger,
                "document_ingestion",
                **observation,
                outcome=outcome,
                terminal_stage=stage,
                ingestion_duration_ms=round(
                    (perf_counter() - ingestion_started) * 1_000, 2
                ),
                embedding_duration_ms=round(embedding_metrics.duration_ms, 2),
                embedding_request_count=embedding_metrics.request_count,
                embedding_input_count=embedding_metrics.input_count,
            )
    return outcome


def _hierarchical_config(
    settings: Settings,
    chunking_version: str | None = None,
) -> HierarchicalChunkingConfig:
    return HierarchicalChunkingConfig(
        child_min_tokens=settings.child_min_tokens,
        child_target_tokens=settings.child_target_tokens,
        child_max_tokens=settings.child_max_tokens,
        child_overlap_tokens=settings.child_overlap_tokens,
        parent_target_tokens=settings.parent_target_tokens,
        parent_max_tokens=settings.parent_max_tokens,
        chunking_version=chunking_version or settings.chunking_version,
    )


def _stage_error(stage: str, safe_message: str) -> str:
    return f"{stage}: {safe_message}"


def _duration_ms(started: float) -> float:
    return round((perf_counter() - started) * 1_000, 2)


async def _mark_failed(
    sessions: async_sessionmaker,
    document_id: uuid.UUID,
    safe_error: str,
) -> None:
    async with sessions() as session:
        document = await session.scalar(
            select(Document).where(Document.id == document_id).with_for_update()
        )
        if document is None or document.status != DocumentStatus.PROCESSING:
            return
        document.status = DocumentStatus.FAILED
        document.safe_error = safe_error[:2_000]
        document.ingestion_stage = "failed"
        await session.commit()


async def _update_progress(
    sessions: async_sessionmaker,
    document_id: uuid.UUID,
    *,
    stage: str,
    progress: int,
    parser_version: str | None = None,
    source_uri: str | None = None,
    source_metadata: dict[str, object] | None = None,
    warnings: list[dict[str, object]] | None = None,
) -> None:
    values: dict[str, object] = {
        "ingestion_stage": stage,
        "ingestion_progress": progress,
        "updated_at": func.now(),
    }
    if parser_version is not None:
        values["parser_version"] = parser_version
    if source_uri is not None:
        values["source_uri"] = source_uri
    if source_metadata is not None:
        values["source_metadata"] = source_metadata
    if warnings is not None:
        values["ingestion_warnings"] = warnings
    async with sessions() as session:
        await session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status == DocumentStatus.PROCESSING)
            .values(**values)
        )
        await session.commit()


def _apply_extracted_provenance(document: Document, extracted: object) -> None:
    metadata = getattr(extracted, "metadata", {})
    if not isinstance(metadata, dict):
        return
    source_metadata = dict(document.source_metadata or {})
    for key in ("module", "title"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            source_metadata[key] = value
    canonical_uri = metadata.get("canonical_uri")
    if document.source_uri is None and isinstance(canonical_uri, str) and canonical_uri:
        document.source_uri = canonical_uri
    document.source_metadata = source_metadata


async def run_purge(document_id: uuid.UUID) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            document = await session.get(Document, document_id)
            if document is None or document.status != DocumentStatus.DELETING:
                return
            storage_key = document.storage_key
        await asyncio.to_thread(remove_stored_file_sync, settings.upload_dir, storage_key)
        async with sessions() as session:
            await session.execute(
                delete(Document).where(
                    Document.id == document_id,
                    Document.status == DocumentStatus.DELETING,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


@celery_app.task(name="mikurag.documents.ingest")
def ingest_document(
    document_id: str,
    target_chunking_version: str | None = None,
) -> None:
    outcome = asyncio.run(
        run_ingestion(
            uuid.UUID(document_id),
            target_chunking_version=target_chunking_version,
        )
    )
    if outcome != "busy":
        return
    try:
        ingest_document.apply_async(
            args=[document_id, target_chunking_version],
            countdown=get_settings().ingestion_busy_retry_seconds,
        )
    except (CeleryError, KombuOperationalError, RedisError, OSError):
        logger.error(
            "Could not reschedule busy Document ingestion for %s; retry it from the UI",
            document_id,
        )


@celery_app.task(name="mikurag.documents.reindex_batch")
def reindex_document_batch(job_id: str) -> None:
    from app.ingestion.reindexing import pause_reindex_job, process_reindex_batch

    parsed_job_id = uuid.UUID(job_id)
    should_continue = asyncio.run(
        process_reindex_batch(parsed_job_id, run_ingestion)
    )
    if not should_continue:
        return
    try:
        reindex_document_batch.apply_async(
            args=[job_id],
            countdown=get_settings().reindex_batch_delay_seconds,
        )
    except (CeleryError, KombuOperationalError, RedisError, OSError):
        logger.error("Could not enqueue the next re-index batch for %s", job_id)
        asyncio.run(
            pause_reindex_job(
                parsed_job_id,
                "The re-index queue became unavailable; resume the job when it recovers.",
            )
        )


@celery_app.task(
    name="mikurag.documents.purge",
    autoretry_for=(OSError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def purge_document(document_id: str) -> None:
    asyncio.run(run_purge(uuid.UUID(document_id)))

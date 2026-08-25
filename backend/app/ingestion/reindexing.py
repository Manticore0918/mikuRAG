import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import (
    Chunk,
    ChunkLevel,
    Document,
    DocumentStatus,
    ReindexItem,
    ReindexItemStatus,
    ReindexJob,
    ReindexJobStatus,
)
from app.observability import emit_observation

IngestDocument = Callable[[uuid.UUID, str | None], Awaitable[object]]
logger = logging.getLogger(__name__)


async def process_reindex_batch(
    job_id: uuid.UUID,
    ingest_document: IngestDocument,
) -> bool:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        target_version, document_ids = await _claim_batch(
            sessions,
            job_id,
            maximum_attempts=settings.reindex_max_attempts,
            stale_after_seconds=settings.reindex_stale_after_seconds,
        )
        for document_id in document_ids:
            await ingest_document(document_id, target_version)
            await _finish_item(
                sessions,
                job_id=job_id,
                document_id=document_id,
                target_version=target_version,
                maximum_attempts=settings.reindex_max_attempts,
            )
        return await _refresh_job(sessions, job_id)
    finally:
        await engine.dispose()


async def pause_reindex_job(job_id: uuid.UUID, safe_error: str) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            job = await session.get(ReindexJob, job_id)
            if job is not None and job.status in {
                ReindexJobStatus.QUEUED,
                ReindexJobStatus.RUNNING,
            }:
                job.status = ReindexJobStatus.PAUSED
                job.last_error = safe_error[:2_000]
                await session.commit()
    finally:
        await engine.dispose()


async def _claim_batch(
    sessions: async_sessionmaker,
    job_id: uuid.UUID,
    *,
    maximum_attempts: int,
    stale_after_seconds: int,
) -> tuple[str, list[uuid.UUID]]:
    async with sessions() as session:
        job = await session.scalar(
            select(ReindexJob).where(ReindexJob.id == job_id).with_for_update()
        )
        if job is None or job.status not in {
            ReindexJobStatus.QUEUED,
            ReindexJobStatus.RUNNING,
        }:
            return "legacy", []
        job.status = ReindexJobStatus.RUNNING
        job.last_error = None
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=stale_after_seconds)
        stale_items = list(
            await session.scalars(
                select(ReindexItem)
                .where(
                    ReindexItem.job_id == job_id,
                    ReindexItem.status == ReindexItemStatus.PROCESSING,
                    ReindexItem.started_at <= stale_before,
                )
                .with_for_update(skip_locked=True)
            )
        )
        for item in stale_items:
            document = await session.get(Document, item.document_id)
            matching_chunks = await session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(
                    Chunk.document_id == item.document_id,
                    Chunk.chunk_level == ChunkLevel.CHILD,
                    Chunk.chunking_version == job.target_chunking_version,
                )
            )
            if (
                document is not None
                and document.status == DocumentStatus.READY
                and bool(matching_chunks)
            ):
                item.status = ReindexItemStatus.COMPLETED
                item.safe_error = None
                item.completed_at = now
                continue
            if document is not None and document.status in {
                DocumentStatus.PENDING,
                DocumentStatus.PROCESSING,
                DocumentStatus.FAILED,
            }:
                document.status = DocumentStatus.READY
                document.safe_error = None
            item.started_at = None
            if item.attempts >= maximum_attempts:
                item.status = ReindexItemStatus.FAILED
                item.safe_error = "Re-indexing did not finish before the retry limit"
                item.completed_at = now
            else:
                item.status = ReindexItemStatus.PENDING
                item.safe_error = "A worker stopped during re-indexing; retrying safely"
                item.completed_at = None
        items = list(
            await session.scalars(
                select(ReindexItem)
                .where(
                    ReindexItem.job_id == job_id,
                    ReindexItem.status == ReindexItemStatus.PENDING,
                )
                .order_by(ReindexItem.document_id)
                .limit(job.batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        claimed: list[uuid.UUID] = []
        for item in items:
            document = await session.get(Document, item.document_id)
            if document is None or document.status not in {
                DocumentStatus.READY,
                DocumentStatus.FAILED,
            }:
                item.status = ReindexItemStatus.FAILED
                item.safe_error = "Document is not available for re-indexing"
                item.completed_at = now
                continue
            document.status = DocumentStatus.PENDING
            document.safe_error = None
            item.status = ReindexItemStatus.PROCESSING
            item.attempts += 1
            item.safe_error = None
            item.started_at = now
            claimed.append(item.document_id)
        await session.commit()
        return job.target_chunking_version, claimed


async def _finish_item(
    sessions: async_sessionmaker,
    *,
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    target_version: str,
    maximum_attempts: int,
) -> None:
    async with sessions() as session:
        item = await session.get(
            ReindexItem,
            {"job_id": job_id, "document_id": document_id},
        )
        document = await session.get(Document, document_id)
        matching_chunks = await session.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(
                Chunk.document_id == document_id,
                Chunk.chunk_level == ChunkLevel.CHILD,
                Chunk.chunking_version == target_version,
            )
        )
        if (
            item is not None
            and document is not None
            and document.status == DocumentStatus.READY
            and bool(matching_chunks)
        ):
            item.status = ReindexItemStatus.COMPLETED
            item.safe_error = None
            item.completed_at = datetime.now(UTC)
        elif (
            item is not None
            and document is not None
            and retryable_reindex_failure(
                document,
                attempts=item.attempts,
                maximum_attempts=maximum_attempts,
            )
        ):
            item.status = ReindexItemStatus.PENDING
            item.safe_error = document.safe_error
            item.started_at = None
            item.completed_at = None
        elif item is not None:
            item.status = ReindexItemStatus.FAILED
            item.safe_error = (
                document.safe_error
                if document is not None and document.safe_error
                else "Re-indexing did not produce the requested chunking version"
            )
            item.completed_at = datetime.now(UTC)
        await session.commit()


def retryable_reindex_failure(
    document: Document,
    *,
    attempts: int,
    maximum_attempts: int,
) -> bool:
    safe_error = document.safe_error or ""
    return (
        document.status == DocumentStatus.FAILED
        and attempts < maximum_attempts
        and safe_error.startswith(("embed:", "persist:"))
    )


async def _refresh_job(
    sessions: async_sessionmaker,
    job_id: uuid.UUID,
) -> bool:
    async with sessions() as session:
        job = await session.scalar(
            select(ReindexJob).where(ReindexJob.id == job_id).with_for_update()
        )
        if job is None:
            return False
        rows = (
            await session.execute(
                select(ReindexItem.status, func.count())
                .where(ReindexItem.job_id == job_id)
                .group_by(ReindexItem.status)
            )
        ).all()
        counts = {status: int(count) for status, count in rows}
        job.completed_documents = counts.get(ReindexItemStatus.COMPLETED, 0)
        job.failed_documents = counts.get(ReindexItemStatus.FAILED, 0)
        pending = counts.get(ReindexItemStatus.PENDING, 0)
        processing = counts.get(ReindexItemStatus.PROCESSING, 0)
        if (
            job.status == ReindexJobStatus.RUNNING
            and pending == 0
            and processing == 0
        ):
            if job.failed_documents:
                job.status = ReindexJobStatus.FAILED
                job.last_error = (
                    f"{job.failed_documents} Document(s) failed re-indexing; "
                    "inspect the per-Document status before retrying."
                )
            else:
                job.status = ReindexJobStatus.COMPLETED
        await session.commit()
        emit_observation(
            logger,
            "reindex_job_progress",
            job_id=str(job.id),
            target_chunking_version=job.target_chunking_version,
            status=job.status,
            total_documents=job.total_documents,
            completed_documents=job.completed_documents,
            failed_documents=job.failed_documents,
            pending_documents=pending,
            processing_documents=processing,
        )
        return job.status == ReindexJobStatus.RUNNING and (
            pending > 0 or processing > 0
        )

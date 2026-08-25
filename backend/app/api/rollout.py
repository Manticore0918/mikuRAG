import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import distinct, func, select, update

from app.config import get_settings
from app.dependencies import Administrator, DatabaseSession
from app.ingestion.dispatch import enqueue_reindex_batch
from app.models import (
    Chunk,
    ChunkLevel,
    Document,
    DocumentStatus,
    ReindexItem,
    ReindexItemStatus,
    ReindexJob,
    ReindexJobStatus,
    ReindexSelection,
)
from app.rollout import configured_rollout_phase, select_canary_documents
from app.schemas import (
    ChunkingRolloutStatusRead,
    ReindexJobCreate,
    ReindexJobRead,
    ReindexRollbackCreate,
)
from app.security import require_csrf

router = APIRouter(prefix="/admin/chunking-rollout", tags=["chunking rollout"])
CsrfCheck = Annotated[None, Depends(require_csrf)]


@router.get("/status", response_model=ChunkingRolloutStatusRead)
async def rollout_status(
    _: Administrator,
    session: DatabaseSession,
) -> ChunkingRolloutStatusRead:
    settings = get_settings()
    version_rows = (
        await session.execute(
            select(Chunk.chunking_version, func.count(distinct(Chunk.document_id)))
            .where(Chunk.chunk_level == ChunkLevel.CHILD)
            .group_by(Chunk.chunking_version)
        )
    ).all()
    active_jobs = list(
        await session.scalars(
            select(ReindexJob)
            .where(
                ReindexJob.status.in_(
                    [
                        ReindexJobStatus.QUEUED,
                        ReindexJobStatus.RUNNING,
                        ReindexJobStatus.PAUSED,
                    ]
                )
            )
            .order_by(ReindexJob.created_at.desc())
        )
    )
    return ChunkingRolloutStatusRead(
        configured_phase=configured_rollout_phase(
            settings,
            active_reindex_job=bool(active_jobs),
        ),
        chunking_version=settings.chunking_version,
        hierarchical_retrieval_enabled=settings.hierarchical_retrieval_enabled,
        summary_generation_enabled=settings.summary_generation_enabled,
        document_version_counts={
            str(version): int(count) for version, count in version_rows
        },
        active_jobs=active_jobs,
    )


@router.post(
    "/reindex-jobs",
    response_model=ReindexJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_reindex_job(
    payload: ReindexJobCreate,
    _csrf: CsrfCheck,
    administrator: Administrator,
    session: DatabaseSession,
) -> ReindexJob:
    documents = await _eligible_documents(
        session,
        target_version=payload.target_chunking_version,
        knowledge_base_id=payload.knowledge_base_id,
    )
    if payload.selection_mode == ReindexSelection.CANARY:
        documents = select_canary_documents(documents, payload.canary_percentage)
    return await _create_job(
        session,
        documents=documents,
        target_version=payload.target_chunking_version,
        selection_mode=payload.selection_mode,
        canary_percentage=payload.canary_percentage,
        batch_size=payload.batch_size,
        knowledge_base_id=payload.knowledge_base_id,
        created_by_id=administrator.id,
    )


@router.get("/reindex-jobs/{job_id}", response_model=ReindexJobRead)
async def read_reindex_job(
    job_id: uuid.UUID,
    _: Administrator,
    session: DatabaseSession,
) -> ReindexJob:
    return await _require_job(session, job_id)


@router.post("/reindex-jobs/{job_id}/pause", response_model=ReindexJobRead)
async def pause_reindex(
    job_id: uuid.UUID,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> ReindexJob:
    job = await _require_job(session, job_id)
    if job.status not in {ReindexJobStatus.QUEUED, ReindexJobStatus.RUNNING}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only queued or running re-index jobs can be paused",
        )
    job.status = ReindexJobStatus.PAUSED
    await session.commit()
    await session.refresh(job)
    return job


@router.post("/reindex-jobs/{job_id}/resume", response_model=ReindexJobRead)
async def resume_reindex(
    job_id: uuid.UUID,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> ReindexJob:
    job = await _require_job(session, job_id)
    if job.status != ReindexJobStatus.PAUSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only paused re-index jobs can be resumed",
        )
    job.status = ReindexJobStatus.QUEUED
    job.last_error = None
    await session.commit()
    if not enqueue_reindex_batch(job.id):
        job.status = ReindexJobStatus.PAUSED
        job.last_error = "The re-index queue is unavailable; resume the job later."
        await session.commit()
    await session.refresh(job)
    return job


@router.post("/reindex-jobs/{job_id}/cancel", response_model=ReindexJobRead)
async def cancel_reindex(
    job_id: uuid.UUID,
    _csrf: CsrfCheck,
    _: Administrator,
    session: DatabaseSession,
) -> ReindexJob:
    job = await _require_job(session, job_id)
    if job.status in {
        ReindexJobStatus.COMPLETED,
        ReindexJobStatus.FAILED,
        ReindexJobStatus.CANCELLED,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The re-index job is already terminal",
        )
    job.status = ReindexJobStatus.CANCELLED
    await session.execute(
        update(ReindexItem)
        .where(
            ReindexItem.job_id == job.id,
            ReindexItem.status == ReindexItemStatus.PENDING,
        )
        .values(status=ReindexItemStatus.CANCELLED)
    )
    await session.commit()
    await session.refresh(job)
    return job


@router.post(
    "/reindex-jobs/{job_id}/rollback",
    response_model=ReindexJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rollback_reindex(
    job_id: uuid.UUID,
    payload: ReindexRollbackCreate,
    _csrf: CsrfCheck,
    administrator: Administrator,
    session: DatabaseSession,
) -> ReindexJob:
    source_job = await _require_job(session, job_id)
    if (
        source_job.status != ReindexJobStatus.COMPLETED
        or source_job.target_chunking_version != "hierarchical_v1"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a completed hierarchical re-index job can be rolled back",
        )
    documents = list(
        await session.scalars(
            select(Document)
            .join(ReindexItem, ReindexItem.document_id == Document.id)
            .where(
                ReindexItem.job_id == source_job.id,
                ReindexItem.status == ReindexItemStatus.COMPLETED,
                Document.status == DocumentStatus.READY,
            )
            .order_by(Document.id)
        )
    )
    return await _create_job(
        session,
        documents=documents,
        target_version="legacy",
        selection_mode=ReindexSelection.ALL,
        canary_percentage=100,
        batch_size=payload.batch_size,
        knowledge_base_id=source_job.knowledge_base_id,
        created_by_id=administrator.id,
    )


async def _eligible_documents(
    session: DatabaseSession,
    *,
    target_version: str,
    knowledge_base_id: uuid.UUID | None,
) -> list[Document]:
    statement = (
        select(Document)
        .join(Chunk, Chunk.document_id == Document.id)
        .where(
            Document.status.in_([DocumentStatus.READY, DocumentStatus.FAILED]),
            Chunk.chunk_level == ChunkLevel.CHILD,
            Chunk.chunking_version != target_version,
        )
        .distinct()
        .order_by(Document.id)
    )
    if knowledge_base_id is not None:
        statement = statement.where(
            Document.knowledge_base_id == knowledge_base_id
        )
    return list(await session.scalars(statement))


async def _create_job(
    session: DatabaseSession,
    *,
    documents: list[Document],
    target_version: str,
    selection_mode: str,
    canary_percentage: int,
    batch_size: int,
    knowledge_base_id: uuid.UUID | None,
    created_by_id: uuid.UUID,
) -> ReindexJob:
    active_job_id = await session.scalar(
        select(ReindexJob.id).where(
            ReindexJob.status.in_(
                [
                    ReindexJobStatus.QUEUED,
                    ReindexJobStatus.RUNNING,
                    ReindexJobStatus.PAUSED,
                ]
            )
        )
    )
    if active_job_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pause, complete, or cancel the active re-index job first",
        )
    if not documents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No eligible Documents require the requested chunking version",
        )
    job = ReindexJob(
        target_chunking_version=target_version,
        selection_mode=selection_mode,
        canary_percentage=canary_percentage,
        batch_size=batch_size,
        status=ReindexJobStatus.QUEUED,
        knowledge_base_id=knowledge_base_id,
        created_by_id=created_by_id,
        total_documents=len(documents),
    )
    session.add(job)
    await session.flush()
    session.add_all(
        [
            ReindexItem(
                job_id=job.id,
                document_id=document.id,
                status=ReindexItemStatus.PENDING,
            )
            for document in documents
        ]
    )
    await session.commit()
    if not enqueue_reindex_batch(job.id):
        job.status = ReindexJobStatus.PAUSED
        job.last_error = "The re-index queue is unavailable; resume the job later."
        await session.commit()
    await session.refresh(job)
    return job


async def _require_job(
    session: DatabaseSession,
    job_id: uuid.UUID,
) -> ReindexJob:
    job = await session.get(ReindexJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Re-index job not found",
        )
    return job

import asyncio
import logging
import uuid

from sqlalchemy import delete, func, literal_column, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.celery_app import celery_app
from app.config import get_settings
from app.ingestion.chunking import chunk_sections
from app.ingestion.embeddings import embed_texts
from app.ingestion.errors import IngestionError
from app.ingestion.extraction import extract_document
from app.ingestion.storage import remove_stored_file_sync, storage_path
from app.models import Chunk, Document, DocumentStatus

logger = logging.getLogger(__name__)


async def run_ingestion(document_id: uuid.UUID) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            claimed = await session.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.status.in_([DocumentStatus.PENDING, DocumentStatus.FAILED]),
                )
                .values(status=DocumentStatus.PROCESSING, safe_error=None)
            )
            await session.commit()
            if claimed.rowcount != 1:
                return

        async with sessions() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return
            path = storage_path(settings.upload_dir, document.storage_key)
            media_type = document.media_type

        extracted = await asyncio.to_thread(
            extract_document,
            path,
            media_type,
            settings.max_document_pages,
        )
        chunks = chunk_sections(
            extracted.sections,
            settings.chunk_target_characters,
            settings.chunk_overlap_characters,
        )
        if not chunks:
            raise IngestionError("No searchable text could be produced from this Document")
        if len(chunks) > settings.max_document_chunks:
            raise IngestionError(
                "The extracted text exceeds the safe Ingestion limit for one Document"
            )
        vectors = await embed_texts([chunk.text for chunk in chunks], settings=settings)

        async with sessions() as session:
            document = await session.scalar(
                select(Document).where(Document.id == document_id).with_for_update()
            )
            if document is None or document.status != DocumentStatus.PROCESSING:
                return
            await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
            session.add_all(
                [
                    Chunk(
                        document_id=document_id,
                        ordinal=ordinal,
                        text=chunk.text,
                        locator=chunk.locator,
                        embedding=vector,
                        embedding_model=settings.embedding_model_id,
                    )
                    for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
                ]
            )
            await session.flush()
            await session.execute(
                update(Chunk)
                .where(Chunk.document_id == document_id)
                .values(
                    search_vector=func.to_tsvector(literal_column("'simple'"), Chunk.text)
                )
            )
            document.page_count = extracted.page_count
            document.status = DocumentStatus.READY
            document.safe_error = None
            await session.commit()
    except IngestionError as error:
        logger.warning("Document ingestion failed for %s: %s", document_id, error.safe_message)
        await _mark_failed(sessions, document_id, error.safe_message)
    except Exception:
        logger.exception("Unexpected Document ingestion failure for %s", document_id)
        await _mark_failed(
            sessions,
            document_id,
            "Ingestion failed unexpectedly. Retry the Document or inspect the worker logs.",
        )
    finally:
        await engine.dispose()


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
        await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        document.status = DocumentStatus.FAILED
        document.safe_error = safe_error[:2_000]
        await session.commit()


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
def ingest_document(document_id: str) -> None:
    asyncio.run(run_ingestion(uuid.UUID(document_id)))


@celery_app.task(
    name="mikurag.documents.purge",
    autoretry_for=(OSError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def purge_document(document_id: str) -> None:
    asyncio.run(run_purge(uuid.UUID(document_id)))

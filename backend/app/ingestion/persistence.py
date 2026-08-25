import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy import delete, func, literal_column, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.chunking import TextChunk
from app.ingestion.errors import IngestionError
from app.ingestion.hierarchical_chunking import ConstructedHierarchy
from app.ingestion.provenance import merge_locator
from app.ingestion.summarization import GeneratedSummary
from app.ingestion.tokenization import Tokenizer
from app.models import Chunk, ChunkContentType, ChunkLevel


@dataclass(frozen=True)
class ChunkModelBatch:
    parents: list[Chunk]
    children: list[Chunk]
    summaries: list[Chunk] = field(default_factory=list)


def build_hierarchical_chunk_models(
    *,
    document_id: uuid.UUID,
    hierarchy: ConstructedHierarchy,
    vectors: list[list[float]],
    embedding_model: str,
    provenance: Mapping[str, object] | None = None,
) -> ChunkModelBatch:
    if len(vectors) != len(hierarchy.children):
        raise IngestionError("The embedding count does not match the child chunk count")

    parent_ids = {
        parent.ordinal: _stable_chunk_id(
            document_id,
            parent.chunk_level,
            parent.ordinal,
            parent.content_hash,
        )
        for parent in hierarchy.parents
    }
    parents = [
        Chunk(
            id=parent_ids[parent.ordinal],
            document_id=document_id,
            parent_chunk_id=None,
            chunk_level=ChunkLevel.PARENT,
            ordinal=parent.ordinal,
            text=parent.text,
            locator=merge_locator(parent.locator, provenance),
            start_page=parent.start_page,
            end_page=parent.end_page,
            start_offset=parent.start_offset,
            end_offset=parent.end_offset,
            heading_path=parent.heading_path,
            content_type=parent.content_type,
            token_count=parent.token_count,
            chunking_version=parent.chunking_version,
            content_hash=parent.content_hash,
            embedding=None,
            embedding_model=None,
        )
        for parent in hierarchy.parents
    ]
    children = [
        Chunk(
            id=_stable_chunk_id(
                document_id,
                child.chunk_level,
                child.ordinal,
                child.content_hash,
            ),
            document_id=document_id,
            parent_chunk_id=parent_ids[child.parent_ordinal],
            chunk_level=ChunkLevel.CHILD,
            ordinal=child.ordinal,
            text=child.text,
            locator=merge_locator(child.locator, provenance),
            start_page=child.start_page,
            end_page=child.end_page,
            start_offset=child.start_offset,
            end_offset=child.end_offset,
            heading_path=child.heading_path,
            content_type=child.content_type,
            token_count=child.token_count,
            chunking_version=child.chunking_version,
            content_hash=child.content_hash,
            embedding=vector,
            embedding_model=embedding_model,
        )
        for child, vector in zip(hierarchy.children, vectors, strict=True)
        if child.parent_ordinal is not None
    ]
    if len(children) != len(hierarchy.children):
        raise IngestionError("A child chunk is missing its parent association")
    return ChunkModelBatch(parents=parents, children=children)


def build_legacy_chunk_models(
    *,
    document_id: uuid.UUID,
    chunks: list[TextChunk],
    vectors: list[list[float]],
    embedding_model: str,
    tokenizer: Tokenizer,
    provenance: Mapping[str, object] | None = None,
) -> ChunkModelBatch:
    if len(vectors) != len(chunks):
        raise IngestionError("The embedding count does not match the legacy chunk count")
    children: list[Chunk] = []
    for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
        start_page = _positive_locator_int(chunk.locator, "start_page", fallback_key="page")
        end_page = _positive_locator_int(chunk.locator, "end_page", fallback_key="page")
        heading_path = _heading_path(chunk.locator)
        content_hash = _legacy_content_hash(chunk.text, chunk.locator)
        children.append(
            Chunk(
                id=_stable_chunk_id(document_id, "child", ordinal, content_hash),
                document_id=document_id,
                parent_chunk_id=None,
                chunk_level=ChunkLevel.CHILD,
                ordinal=ordinal,
                text=chunk.text,
                locator=merge_locator(chunk.locator, provenance),
                start_page=start_page,
                end_page=end_page,
                heading_path=heading_path,
                content_type=ChunkContentType.MIXED,
                token_count=tokenizer.count(chunk.text),
                chunking_version="legacy",
                content_hash=content_hash,
                embedding=vector,
                embedding_model=embedding_model,
            )
        )
    return ChunkModelBatch(parents=[], children=children)


def build_summary_chunk_models(
    *,
    document_id: uuid.UUID,
    parents: list[Chunk],
    summaries: list[GeneratedSummary],
    vectors: list[list[float]],
    embedding_model: str,
    provenance: Mapping[str, object] | None = None,
) -> list[Chunk]:
    if len(vectors) != len(summaries):
        raise IngestionError("The embedding count does not match the summary count")
    parent_ids = {parent.ordinal: parent.id for parent in parents}
    models: list[Chunk] = []
    for summary, vector in zip(summaries, vectors, strict=True):
        source_parent_id = (
            parent_ids.get(summary.source_parent_ordinal)
            if summary.source_parent_ordinal is not None
            else None
        )
        if summary.source_parent_ordinal is not None and source_parent_id is None:
            raise IngestionError("A section summary is missing its source parent")
        locator: dict[str, object] = {
            "start_page": summary.start_page,
            "end_page": summary.end_page,
            "heading_path": summary.heading_path,
            "source_document_id": str(document_id),
            "summary_model": summary.summary_model,
            "summary_prompt_version": summary.prompt_version,
            "source_content_hash": summary.source_content_hash,
        }
        if summary.start_page is not None and summary.start_page == summary.end_page:
            locator["page"] = summary.start_page
        if source_parent_id is not None:
            locator["source_parent_id"] = str(source_parent_id)
        locator = merge_locator(locator, provenance)
        models.append(
            Chunk(
                id=_stable_chunk_id(
                    document_id,
                    summary.chunk_level,
                    summary.ordinal,
                    summary.content_hash,
                ),
                document_id=document_id,
                parent_chunk_id=source_parent_id,
                chunk_level=summary.chunk_level,
                ordinal=summary.ordinal,
                text=summary.text,
                locator=locator,
                start_page=summary.start_page,
                end_page=summary.end_page,
                heading_path=summary.heading_path,
                content_type=ChunkContentType.SUMMARY,
                token_count=summary.token_count,
                chunking_version=summary.chunking_version,
                content_hash=summary.content_hash,
                embedding=vector,
                embedding_model=embedding_model,
            )
        )
    return models


async def replace_document_chunks(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    batch: ChunkModelBatch,
) -> None:
    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    if batch.parents:
        session.add_all(batch.parents)
        await session.flush()
    if batch.summaries:
        session.add_all(batch.summaries)
        await session.flush()
    session.add_all(batch.children)
    await session.flush()
    await session.execute(
        update(Chunk)
        .where(
            Chunk.document_id == document_id,
            Chunk.chunk_level.in_(
                [
                    ChunkLevel.CHILD,
                    ChunkLevel.SECTION_SUMMARY,
                    ChunkLevel.DOCUMENT_SUMMARY,
                ]
            ),
        )
        .values(search_vector=func.to_tsvector(literal_column("'simple'"), Chunk.text))
    )


def _stable_chunk_id(
    document_id: uuid.UUID,
    chunk_level: str,
    ordinal: int,
    content_hash: str,
) -> uuid.UUID:
    return uuid.uuid5(document_id, f"{chunk_level}:{ordinal}:{content_hash}")


def _legacy_content_hash(text: str, locator: dict[str, object]) -> str:
    payload = json.dumps(
        {
            "text": text,
            "locator": locator,
            "chunk_level": "child",
            "chunking_version": "legacy",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _positive_locator_int(
    locator: dict[str, object],
    key: str,
    *,
    fallback_key: str,
) -> int | None:
    value = locator.get(key, locator.get(fallback_key))
    return value if type(value) is int and value > 0 else None


def _heading_path(locator: dict[str, object]) -> list[str]:
    value = locator.get("heading_path")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return []
    return value

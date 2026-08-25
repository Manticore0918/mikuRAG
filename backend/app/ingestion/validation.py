from collections import Counter

from app.ingestion.contracts import ExtractedDocument
from app.ingestion.errors import IngestionError
from app.ingestion.hierarchical_chunking import (
    ConstructedChunk,
    ConstructedHierarchy,
    HierarchicalChunkingConfig,
)
from app.ingestion.tokenization import Tokenizer


def validate_document_limits(
    document: ExtractedDocument,
    *,
    tokenizer: Tokenizer,
    max_document_tokens: int,
) -> None:
    token_count = sum(tokenizer.count(block.text) for block in document.blocks)
    if token_count > max_document_tokens:
        raise IngestionError(
            "The normalized text exceeds the safe token limit for one Document"
        )


def validate_hierarchy(
    document: ExtractedDocument,
    hierarchy: ConstructedHierarchy,
    *,
    config: HierarchicalChunkingConfig,
    tokenizer: Tokenizer,
    max_document_chunks: int,
    max_document_tokens: int,
) -> None:
    validate_document_limits(
        document,
        tokenizer=tokenizer,
        max_document_tokens=max_document_tokens,
    )
    if not hierarchy.parents or not hierarchy.children:
        raise IngestionError("Hierarchical chunking produced no searchable children")
    if len(hierarchy.parents) + len(hierarchy.children) > max_document_chunks:
        raise IngestionError(
            "The hierarchical output exceeds the safe chunk limit for one Document"
        )
    if hierarchy.tokenizer_name != tokenizer.name:
        raise IngestionError("Hierarchical chunk validation used a different tokenizer")

    _validate_ordinals(hierarchy.parents, "parent")
    _validate_ordinals(hierarchy.children, "child")
    _validate_ranges(hierarchy.parents, document.page_count)
    _validate_ranges(hierarchy.children, document.page_count)
    _validate_ordered_offsets(hierarchy.parents, "parent")
    _validate_ordered_offsets(hierarchy.children, "child")
    _validate_sizes(hierarchy, config, tokenizer)
    _validate_parent_links(hierarchy)
    _validate_coverage(document, hierarchy)


def _validate_ordinals(chunks: list[ConstructedChunk], level: str) -> None:
    expected = list(range(len(chunks)))
    actual = [chunk.ordinal for chunk in chunks]
    if actual != expected or any(chunk.chunk_level != level for chunk in chunks):
        raise IngestionError(f"Invalid {level} chunk ordering")


def _validate_ranges(
    chunks: list[ConstructedChunk],
    page_count: int | None,
) -> None:
    for chunk in chunks:
        if chunk.start_page is not None and chunk.start_page <= 0:
            raise IngestionError("Chunk page ranges must be positive")
        if chunk.end_page is not None and chunk.end_page <= 0:
            raise IngestionError("Chunk page ranges must be positive")
        if (
            chunk.start_page is not None
            and chunk.end_page is not None
            and chunk.end_page < chunk.start_page
        ):
            raise IngestionError("Chunk page ranges are reversed")
        if page_count is not None and chunk.end_page is not None and chunk.end_page > page_count:
            raise IngestionError("Chunk page ranges exceed the Document page count")
        if chunk.start_offset is not None and chunk.start_offset < 0:
            raise IngestionError("Chunk offsets cannot be negative")
        if chunk.end_offset is not None and chunk.end_offset < 0:
            raise IngestionError("Chunk offsets cannot be negative")
        if (
            chunk.start_offset is not None
            and chunk.end_offset is not None
            and chunk.end_offset < chunk.start_offset
        ):
            raise IngestionError("Chunk offsets are reversed")


def _validate_ordered_offsets(chunks: list[ConstructedChunk], level: str) -> None:
    offsets = [chunk.start_offset for chunk in chunks if chunk.start_offset is not None]
    if offsets != sorted(offsets):
        raise IngestionError(f"{level.title()} chunk offsets are not ordered")


def _validate_sizes(
    hierarchy: ConstructedHierarchy,
    config: HierarchicalChunkingConfig,
    tokenizer: Tokenizer,
) -> None:
    for parent in hierarchy.parents:
        if parent.token_count != tokenizer.count(parent.text):
            raise IngestionError("Parent token metadata is inconsistent")
        if parent.token_count > config.parent_max_tokens:
            raise IngestionError("A parent chunk exceeds the configured token maximum")
        if parent.chunking_version != config.chunking_version:
            raise IngestionError("A parent has an unexpected chunking version")
        if len(parent.content_hash) != 64:
            raise IngestionError("A parent content hash is invalid")
    for child in hierarchy.children:
        if child.token_count != tokenizer.count(child.text):
            raise IngestionError("Child token metadata is inconsistent")
        if child.token_count > config.child_max_tokens:
            raise IngestionError("A child chunk exceeds the configured token maximum")
        if tokenizer.count(child.embedding_text or child.text) > config.child_max_tokens:
            raise IngestionError("A child embedding input exceeds the token maximum")
        if child.chunking_version != config.chunking_version:
            raise IngestionError("A child has an unexpected chunking version")
        if len(child.content_hash) != 64:
            raise IngestionError("A child content hash is invalid")


def _validate_parent_links(hierarchy: ConstructedHierarchy) -> None:
    child_counts = Counter(child.parent_ordinal for child in hierarchy.children)
    for parent in hierarchy.parents:
        if child_counts[parent.ordinal] == 0:
            raise IngestionError("Every parent must have at least one child")
    for child in hierarchy.children:
        if child.parent_ordinal is None or not 0 <= child.parent_ordinal < len(
            hierarchy.parents
        ):
            raise IngestionError("A child references an invalid parent")
        parent = hierarchy.parents[child.parent_ordinal]
        if not set(child.source_block_orders) <= set(parent.source_block_orders):
            raise IngestionError("A child contains content outside its parent")
        if (
            parent.start_page is not None
            and child.start_page is not None
            and child.start_page < parent.start_page
        ):
            raise IngestionError("A child page range starts before its parent")
        if (
            parent.end_page is not None
            and child.end_page is not None
            and child.end_page > parent.end_page
        ):
            raise IngestionError("A child page range ends after its parent")


def _validate_coverage(
    document: ExtractedDocument,
    hierarchy: ConstructedHierarchy,
) -> None:
    source_orders = {block.order for block in document.blocks if block.text.strip()}
    parent_orders = {
        source_order
        for parent in hierarchy.parents
        for source_order in parent.source_block_orders
    }
    child_orders = {
        source_order
        for child in hierarchy.children
        for source_order in child.source_block_orders
    }
    if source_orders != parent_orders:
        raise IngestionError("Parent construction omitted normalized source content")
    if not source_orders <= child_orders:
        raise IngestionError("Child construction omitted normalized source content")

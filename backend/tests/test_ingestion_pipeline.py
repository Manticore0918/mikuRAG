import uuid
from dataclasses import replace

import pytest
from sqlalchemy.sql.dml import Delete, Update

from app.ingestion.chunking import TextChunk
from app.ingestion.contracts import ExtractedBlock, ExtractedDocument
from app.ingestion.errors import IngestionError
from app.ingestion.hierarchical_chunking import (
    ConstructedHierarchy,
    HierarchicalChunkingConfig,
    construct_hierarchy,
)
from app.ingestion.persistence import (
    build_hierarchical_chunk_models,
    build_legacy_chunk_models,
    replace_document_chunks,
)
from app.ingestion.tasks import _mark_failed, _stage_error
from app.ingestion.tokenization import ConservativeTokenizer
from app.ingestion.validation import validate_hierarchy
from app.models import ChunkLevel, DocumentStatus


def hierarchy_fixture() -> tuple[
    ExtractedDocument,
    ConstructedHierarchy,
    HierarchicalChunkingConfig,
    ConservativeTokenizer,
]:
    tokenizer = ConservativeTokenizer()
    config = HierarchicalChunkingConfig(
        child_min_tokens=3,
        child_target_tokens=5,
        child_max_tokens=8,
        child_overlap_tokens=1,
        parent_target_tokens=16,
        parent_max_tokens=24,
        chunking_version="hierarchical_v1",
    )
    document = ExtractedDocument(
        blocks=[
            ExtractedBlock(
                text="Operations",
                block_type="heading",
                order=0,
                start_page=1,
                end_page=1,
                heading_level=1,
                heading_path=["Operations"],
                metadata={"start_offset": 0, "end_offset": 10},
            ),
            ExtractedBlock(
                text="a1 a2 a3 a4. a5 a6 a7 a8.",
                block_type="paragraph",
                order=1,
                start_page=1,
                end_page=2,
                heading_path=["Operations"],
                metadata={"start_offset": 12, "end_offset": 39},
            ),
        ],
        page_count=2,
    )
    hierarchy = construct_hierarchy(document, config=config, tokenizer=tokenizer)
    return document, hierarchy, config, tokenizer


def test_hierarchy_validation_checks_limits_links_and_coverage() -> None:
    document, hierarchy, config, tokenizer = hierarchy_fixture()

    validate_hierarchy(
        document,
        hierarchy,
        config=config,
        tokenizer=tokenizer,
        max_document_chunks=100,
        max_document_tokens=1_000,
    )

    with pytest.raises(IngestionError, match="no searchable children"):
        validate_hierarchy(
            document,
            replace(hierarchy, children=[]),
            config=config,
            tokenizer=tokenizer,
            max_document_chunks=100,
            max_document_tokens=1_000,
        )

    omitted = replace(
        hierarchy,
        children=[
            replace(child, source_block_orders=(0,))
            for child in hierarchy.children
        ],
    )
    with pytest.raises(IngestionError, match="omitted normalized source content"):
        validate_hierarchy(
            document,
            omitted,
            config=config,
            tokenizer=tokenizer,
            max_document_chunks=100,
            max_document_tokens=1_000,
        )


def test_hierarchical_models_embed_only_children_and_use_stable_ids() -> None:
    _, hierarchy, _, _ = hierarchy_fixture()
    document_id = uuid.uuid4()
    vectors = [[0.1] * 768 for _ in hierarchy.children]

    first = build_hierarchical_chunk_models(
        document_id=document_id,
        hierarchy=hierarchy,
        vectors=vectors,
        embedding_model="embed-v1",
    )
    second = build_hierarchical_chunk_models(
        document_id=document_id,
        hierarchy=hierarchy,
        vectors=vectors,
        embedding_model="embed-v1",
    )

    assert [chunk.id for chunk in first.parents] == [chunk.id for chunk in second.parents]
    assert [chunk.id for chunk in first.children] == [chunk.id for chunk in second.children]
    assert all(chunk.embedding is None for chunk in first.parents)
    assert all(chunk.embedding_model is None for chunk in first.parents)
    assert all(chunk.embedding is not None for chunk in first.children)
    assert all(chunk.parent_chunk_id == first.parents[0].id for chunk in first.children)


def test_legacy_models_receive_hashes_tokens_and_compatibility_pages() -> None:
    document_id = uuid.uuid4()
    chunks = [
        TextChunk(
            text="legacy text",
            locator={"page": 7, "part": 1},
        )
    ]

    batch = build_legacy_chunk_models(
        document_id=document_id,
        chunks=chunks,
        vectors=[[0.2] * 768],
        embedding_model="embed-v1",
        tokenizer=ConservativeTokenizer(),
    )

    child = batch.children[0]
    assert child.chunk_level == ChunkLevel.CHILD
    assert child.start_page == child.end_page == 7
    assert child.token_count is not None
    assert len(child.content_hash or "") == 64


@pytest.mark.asyncio
async def test_replacement_writes_parents_before_children_in_one_session() -> None:
    _, hierarchy, _, _ = hierarchy_fixture()
    batch = build_hierarchical_chunk_models(
        document_id=uuid.uuid4(),
        hierarchy=hierarchy,
        vectors=[[0.1] * 768 for _ in hierarchy.children],
        embedding_model="embed-v1",
    )

    class Session:
        def __init__(self) -> None:
            self.statements = []
            self.batches = []
            self.flushes = 0

        async def execute(self, statement):
            self.statements.append(statement)

        def add_all(self, values) -> None:
            self.batches.append(list(values))

        async def flush(self) -> None:
            self.flushes += 1

    session = Session()
    await replace_document_chunks(
        session,
        document_id=batch.children[0].document_id,
        batch=batch,
    )

    assert isinstance(session.statements[0], Delete)
    assert isinstance(session.statements[-1], Update)
    assert session.batches == [batch.parents, batch.children]
    assert session.flushes == 2


@pytest.mark.asyncio
async def test_mark_failed_preserves_previous_chunks() -> None:
    class Document:
        status = DocumentStatus.PROCESSING
        safe_error = None

    document = Document()

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        async def scalar(self, statement):
            return document

        async def execute(self, statement) -> None:
            raise AssertionError("failure handling must not delete existing chunks")

        async def commit(self) -> None:
            pass

    class Sessions:
        def __call__(self):
            return Session()

    await _mark_failed(Sessions(), uuid.uuid4(), "embed: provider unavailable")

    assert document.status == DocumentStatus.FAILED
    assert document.safe_error == "embed: provider unavailable"
    assert _stage_error("validate", "bad hierarchy") == "validate: bad hierarchy"

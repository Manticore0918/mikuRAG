from sqlalchemy import CheckConstraint, UniqueConstraint

from app.models import Base, ReindexJobStatus


def test_initial_schema_contains_all_approved_core_records() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "knowledge_bases",
        "knowledge_base_access",
        "documents",
        "reindex_jobs",
        "reindex_items",
        "upload_sessions",
        "upload_part_receipts",
        "chunks",
        "conversations",
        "messages",
        "citations",
        "provider_configurations",
    }


def test_users_have_server_checked_session_versions() -> None:
    assert "session_version" in Base.metadata.tables["users"].columns


def test_documents_have_status_scoped_knowledge_base_index() -> None:
    indexes = {index.name for index in Base.metadata.tables["documents"].indexes}
    assert "documents_kb_status_idx" in indexes


def test_upload_sessions_have_checkpoint_and_open_digest_constraints() -> None:
    table = Base.metadata.tables["upload_sessions"]
    assert {"received_bytes", "expires_at", "resulting_document_id"} <= {
        column.name for column in table.columns
    }
    indexes = {index.name for index in table.indexes}
    assert "upload_sessions_open_kb_sha256_uq" in indexes
    receipts = Base.metadata.tables["upload_part_receipts"]
    assert {column.name for column in receipts.primary_key.columns} == {
        "upload_session_id",
        "offset_bytes",
    }


def test_messages_have_sequence_and_single_active_turn_constraints() -> None:
    table = Base.metadata.tables["messages"]
    assert "sequence" in table.columns
    constraints = {constraint.name for constraint in table.constraints}
    indexes = {index.name for index in table.indexes}
    assert "messages_conversation_sequence_uq" in constraints
    assert "messages_one_streaming_assistant_uq" in indexes


def test_chunks_support_hierarchical_levels_and_source_ranges() -> None:
    table = Base.metadata.tables["chunks"]
    assert {
        "parent_chunk_id",
        "chunk_level",
        "start_page",
        "end_page",
        "start_offset",
        "end_offset",
        "heading_path",
        "content_type",
        "token_count",
        "chunking_version",
        "content_hash",
    } <= {column.name for column in table.columns}
    assert table.c.embedding_model.nullable
    assert not table.c.chunk_level.nullable
    assert not table.c.heading_path.nullable
    assert not table.c.content_type.nullable
    assert not table.c.chunking_version.nullable


def test_chunks_have_level_scoped_ordering_and_metadata_constraints() -> None:
    table = Base.metadata.tables["chunks"]
    unique_constraints = {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    check_constraints = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    indexes = {index.name for index in table.indexes}

    assert unique_constraints["chunks_document_level_ordinal_uq"] == (
        "document_id",
        "chunk_level",
        "ordinal",
    )
    assert "chunks_document_ordinal_uq" not in unique_constraints
    assert "ix_chunks_parent_chunk_id" in indexes
    assert {
        "chunks_level_ck",
        "chunks_content_type_ck",
        "chunks_parent_not_self_ck",
        "chunks_page_range_ck",
        "chunks_offset_range_ck",
        "chunks_heading_path_array_ck",
        "chunks_content_hash_length_ck",
    } <= check_constraints


def test_reindex_jobs_have_bounded_batches_and_per_document_progress() -> None:
    jobs = Base.metadata.tables["reindex_jobs"]
    items = Base.metadata.tables["reindex_items"]
    job_checks = {
        constraint.name
        for constraint in jobs.constraints
        if isinstance(constraint, CheckConstraint)
    }
    item_checks = {
        constraint.name
        for constraint in items.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert {
        "target_chunking_version",
        "selection_mode",
        "canary_percentage",
        "batch_size",
        "status",
        "total_documents",
        "completed_documents",
        "failed_documents",
    } <= {column.name for column in jobs.columns}
    assert {column.name for column in items.primary_key.columns} == {
        "job_id",
        "document_id",
    }
    assert {
        "reindex_jobs_target_version_ck",
        "reindex_jobs_batch_size_ck",
        "reindex_jobs_counts_ck",
    } <= job_checks
    assert {
        "reindex_items_status_ck",
        "reindex_items_attempts_ck",
    } <= item_checks
    assert set(ReindexJobStatus) == {
        "queued",
        "running",
        "paused",
        "completed",
        "failed",
        "cancelled",
    }

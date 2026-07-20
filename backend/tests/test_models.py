from app.models import Base


def test_initial_schema_contains_all_approved_core_records() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "knowledge_bases",
        "knowledge_base_access",
        "documents",
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

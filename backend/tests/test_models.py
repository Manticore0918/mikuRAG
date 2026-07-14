from app.models import Base


def test_initial_schema_contains_all_approved_core_records() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "knowledge_bases",
        "knowledge_base_access",
        "documents",
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


def test_messages_have_sequence_and_single_active_turn_constraints() -> None:
    table = Base.metadata.tables["messages"]
    assert "sequence" in table.columns
    constraints = {constraint.name for constraint in table.constraints}
    indexes = {index.name for index in table.indexes}
    assert "messages_conversation_sequence_uq" in constraints
    assert "messages_one_streaming_assistant_uq" in indexes

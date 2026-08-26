from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.ingestion.provenance import chunk_provenance
from app.schemas import UploadSessionCreate


def upload_payload(**updates: object) -> UploadSessionCreate:
    values: dict[str, object] = {
        "original_name": "worker.py",
        "size_bytes": 120,
        "sha256": "a" * 64,
    }
    values.update(updates)
    return UploadSessionCreate(**values)


def test_upload_source_metadata_is_normalized_and_validated() -> None:
    payload = upload_payload(
        language=" PYTHON ",
        tags=["Operations", "operations", "Runbook"],
        source_uri=" https://docs.example.test/runbook ",
        source_path="src\\jobs\\worker.py",
        metadata={"title": "Worker runbook", "revision": 4, "owners": ["ops"]},
    )

    assert payload.language == "python"
    assert payload.tags == ["Operations", "Runbook"]
    assert payload.source_uri == "https://docs.example.test/runbook"
    assert payload.source_path == "src/jobs/worker.py"
    assert payload.metadata["revision"] == 4


@pytest.mark.parametrize(
    "updates",
    [
        {"source_path": "../private/key.py"},
        {"source_uri": "file:///etc/passwd"},
        {"metadata": {"api_token": "do-not-store"}},
        {"metadata": {"nested": {"private_key": "do-not-store"}}},
    ],
)
def test_upload_source_metadata_rejects_unsafe_values(updates: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        upload_payload(**updates)


def test_chunks_receive_only_retrieval_relevant_non_secret_provenance() -> None:
    document = SimpleNamespace(
        source_kind="code",
        source_path="src/jobs/worker.py",
        source_uri="https://git.example.test/worker.py",
        language="python",
        tags=["operations"],
        source_metadata={
            "title": "Worker",
            "repository": "mikuRAG",
            "passage_id": "worker-restore-p1",
            "owner_email": "private@example.test",
            "internal_notes": {"queue": "critical"},
        },
    )

    provenance = chunk_provenance(document)

    assert provenance["source_title"] == "Worker"
    assert provenance["source_repository"] == "mikuRAG"
    assert provenance["source_passage_id"] == "worker-restore-p1"
    assert "owner_email" not in provenance
    assert "internal_notes" not in provenance

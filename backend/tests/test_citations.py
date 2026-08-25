import uuid

from app.models import Citation
from app.rag.citations import public_locator
from app.rag.retrieval import _to_evidence
from app.rag.retrieval_types import Candidate, Evidence
from app.rag.service import citation_payload
from app.schemas import CitationRead


def test_public_locator_preserves_legacy_page_and_adds_range_fields() -> None:
    locator = public_locator(
        {
            "page": 14,
            "section": "Setup",
            "source_parent_id": str(uuid.uuid4()),
        }
    )

    assert locator == {
        "section": "Setup",
        "start_page": 14,
        "end_page": 14,
        "page": 14,
    }


def test_public_locator_emits_cross_page_range_heading_and_no_parent_ids() -> None:
    locator = public_locator(
        {
            "page": 14,
            "start_page": 14,
            "end_page": 15,
            "heading_path": ["Guide", "Setup"],
            "parent_chunk_id": str(uuid.uuid4()),
        }
    )

    assert locator == {
        "start_page": 14,
        "end_page": 15,
        "heading_path": ["Guide", "Setup"],
    }


def test_evidence_shape_is_unchanged_while_locator_is_enriched() -> None:
    chunk_id = uuid.uuid4()
    candidate = Candidate(
        chunk_id=chunk_id,
        document_id=uuid.uuid4(),
        document_name="guide.pdf",
        locator={"section": "Setup", "source_parent_id": str(uuid.uuid4())},
        text="Install the package.",
        start_page=3,
        end_page=4,
        heading_path=["Guide", "Setup"],
        fused_score=0.5,
    )

    evidence = _to_evidence([candidate])[0]

    assert list(Evidence.__dataclass_fields__) == [
        "evidence_id",
        "chunk_id",
        "document_id",
        "document_name",
        "locator",
        "text",
        "retrieval_rank",
        "retrieval_score",
        "semantic_similarity",
        "lexical_score",
    ]
    assert evidence.chunk_id == chunk_id
    assert evidence.locator == {
        "section": "Setup",
        "start_page": 3,
        "end_page": 4,
        "heading_path": ["Guide", "Setup"],
    }


def test_citation_payload_sanitizes_existing_rows_and_schema_accepts_heading_path() -> None:
    citation = Citation(
        id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_name="guide.pdf",
        locator={
            "start_page": 8,
            "end_page": 8,
            "heading_path": ["Guide"],
            "source_parent_id": str(uuid.uuid4()),
        },
        excerpt="Relevant source text.",
        retrieval_rank=1,
        retrieval_score=0.5,
    )

    payload = citation_payload(citation, uuid.uuid4())
    validated = CitationRead.model_validate(payload)

    assert validated.locator == {
        "start_page": 8,
        "end_page": 8,
        "page": 8,
        "heading_path": ["Guide"],
    }
    assert "source_parent_id" not in validated.locator

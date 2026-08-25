from pathlib import Path

import app.ingestion.extraction as extraction
from app.ingestion.contracts import ExtractedBlock, ExtractedDocument
from app.ingestion.normalization import normalize_document, normalize_text


def block(
    text: str,
    order: int,
    *,
    page: int | None = None,
    block_type: str = "paragraph",
    page_position: str | None = None,
) -> ExtractedBlock:
    metadata = {}
    if page_position is not None:
        metadata["page_position"] = page_position
    return ExtractedBlock(
        text=text,
        block_type=block_type,
        order=order,
        start_page=page,
        end_page=page,
        metadata=metadata,
    )


def test_normalize_text_handles_unicode_whitespace_and_line_hyphenation() -> None:
    assert normalize_text("Ａ  value\r\ncontin-\nued\n\nNext") == (
        "A value continued\n\nNext"
    )
    assert normalize_text("  x = 1  \n    y = 2\n", "code") == "  x = 1\n    y = 2"
    assert normalize_text("A  |  B\n1  |  2", "table") == "A | B\n1 | 2"


def test_normalization_joins_cross_page_hyphenation_and_tracks_offsets() -> None:
    document = ExtractedDocument(
        blocks=[
            block("The inter-", 4, page=1),
            block("national standard applies.", 9, page=2),
        ],
        page_count=2,
    )

    normalized = normalize_document(document)

    assert len(normalized.blocks) == 1
    result = normalized.blocks[0]
    assert result.text == "The international standard applies."
    assert (result.start_page, result.end_page) == (1, 2)
    assert result.order == 0
    assert result.metadata["source_orders"] == [4, 9]
    assert result.metadata["start_offset"] == 0
    assert result.metadata["end_offset"] == len(result.text)
    assert normalized.sections[0].locator == {"start_page": 1, "end_page": 2}


def test_normalization_removes_only_repeated_page_furniture_with_warnings() -> None:
    document = ExtractedDocument(
        blocks=[
            block("MikuRAG Guide", 0, page=1, page_position="header"),
            block("First page content.", 1, page=1),
            block("MikuRAG Guide", 2, page=2, page_position="header"),
            block("Second page content.", 3, page=2),
        ],
        page_count=2,
    )

    normalized = normalize_document(document)

    assert [item.text for item in normalized.blocks] == [
        "First page content.",
        "Second page content.",
    ]
    assert [warning.code for warning in normalized.warnings] == [
        "repeated_header_removed",
        "repeated_header_removed",
    ]
    assert normalize_document(normalized) == normalized


def test_pdf_extraction_records_empty_pages_and_fallback_usage(
    monkeypatch,
) -> None:
    class Page:
        def __init__(self, text: str) -> None:
            self.text = text

        def extract_text(self) -> str:
            return self.text

    class Reader:
        is_encrypted = False
        pages = [Page(""), Page("tiny")]

    class Fallback:
        def extract_page(self, path: Path, page_number: int) -> str | None:
            if page_number == 2:
                return "Fallback paragraph with enough content. " * 4
            return None

    monkeypatch.setattr(extraction, "PdfReader", lambda _: Reader())

    extracted = extraction.extract_pdf(
        Path("unused.pdf"),
        max_pages=10,
        fallback_extractor=Fallback(),
    )

    assert [warning.code for warning in extracted.warnings] == [
        "empty_page",
        "ocr_fallback_used",
    ]
    assert extracted.blocks[0].start_page == 2
    assert "Fallback paragraph" in extracted.blocks[0].text

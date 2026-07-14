import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from docx import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from app.ingestion.errors import ExtractionError


@dataclass(frozen=True)
class ExtractedSection:
    text: str
    locator: dict[str, int | str]


@dataclass(frozen=True)
class ExtractedDocument:
    sections: list[ExtractedSection]
    page_count: int | None


def _require_content(sections: list[ExtractedSection]) -> None:
    if not any(section.text.strip() for section in sections):
        raise ExtractionError(
            "No extractable text was found. Scanned or image-only Documents require OCR, "
            "which is not supported in this MVP."
        )


def extract_pdf(path: Path, max_pages: int) -> ExtractedDocument:
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ExtractionError("Password-protected PDF Documents are not supported")
        page_count = len(reader.pages)
        if page_count > max_pages:
            raise ExtractionError(f"Documents cannot exceed {max_pages} pages")
        sections = [
            ExtractedSection(
                text=(page.extract_text() or "").strip(),
                locator={"page": page_number},
            )
            for page_number, page in enumerate(reader.pages, start=1)
        ]
    except ExtractionError:
        raise
    except Exception as error:
        raise ExtractionError("The PDF could not be read safely") from error
    _require_content(sections)
    return ExtractedDocument(sections=sections, page_count=page_count)


def _docx_page_count(path: Path) -> int | None:
    try:
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("docProps/app.xml"))
        pages = root.find("{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}Pages")
        return int(pages.text) if pages is not None and pages.text else None
    except (KeyError, ValueError, zipfile.BadZipFile, ElementTree.ParseError):
        return None


def extract_docx(path: Path, max_pages: int) -> ExtractedDocument:
    page_count = _docx_page_count(path)
    if page_count is not None and page_count > max_pages:
        raise ExtractionError(f"Documents cannot exceed {max_pages} pages")
    try:
        document = DocxDocument(path)
        sections: list[ExtractedSection] = []
        section_number = 0
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                text = block.text.strip()
            elif isinstance(block, Table):
                text = "\n".join(
                    " | ".join(cell.text.strip() for cell in row.cells) for row in block.rows
                ).strip()
            else:
                continue
            if text:
                section_number += 1
                sections.append(
                    ExtractedSection(text=text, locator={"section": section_number})
                )
    except Exception as error:
        raise ExtractionError("The DOCX Document could not be read safely") from error
    _require_content(sections)
    return ExtractedDocument(sections=sections, page_count=page_count)


def extract_text(path: Path, markdown: bool) -> ExtractedDocument:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise ExtractionError("The text Document could not be read as UTF-8") from error

    sections: list[ExtractedSection] = []
    if markdown:
        current_heading = "Document"
        buffer: list[str] = []
        start_line = 1

        def flush(end_line: int) -> None:
            text = "\n".join(buffer).strip()
            if text:
                sections.append(
                    ExtractedSection(
                        text=text,
                        locator={
                            "section": current_heading,
                            "line_start": start_line,
                            "line_end": end_line,
                        },
                    )
                )

        for line_number, line in enumerate(content.splitlines(), start=1):
            heading = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
            if heading:
                flush(line_number - 1)
                current_heading = heading.group(1)
                buffer = [line]
                start_line = line_number
            else:
                buffer.append(line)
        flush(max(1, len(content.splitlines())))
    else:
        blocks = re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", content, re.S)
        for block_number, match in enumerate(blocks, start=1):
            line_start = content.count("\n", 0, match.start()) + 1
            line_end = content.count("\n", 0, match.end()) + 1
            sections.append(
                ExtractedSection(
                    text=match.group(0).strip(),
                    locator={
                        "section": block_number,
                        "line_start": line_start,
                        "line_end": line_end,
                    },
                )
            )
    _require_content(sections)
    return ExtractedDocument(sections=sections, page_count=None)


def extract_document(path: Path, media_type: str, max_pages: int) -> ExtractedDocument:
    if media_type == "application/pdf":
        return extract_pdf(path, max_pages)
    if media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return extract_docx(path, max_pages)
    return extract_text(path, markdown=media_type == "text/markdown")

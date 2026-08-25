import re
import unicodedata
from collections import defaultdict
from dataclasses import replace

from app.ingestion.contracts import ExtractedBlock, ExtractedDocument, ExtractionWarning
from app.ingestion.errors import ExtractionError

_REPEATED_PAGE_POSITIONS = {"header", "footer"}


def normalize_text(text: str, block_type: str = "paragraph") -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    normalized = re.sub(r"(?<=\w)-[ \t]*\n[ \t]*(?=\w)", "", normalized)

    if block_type == "code":
        lines = [line.rstrip() for line in normalized.splitlines()]
        return "\n".join(lines).strip("\n")
    if block_type == "table":
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in normalized.splitlines()]
        return "\n".join(line for line in lines if line).strip()

    paragraphs = re.split(r"\n[ \t]*\n+", normalized)
    normalized_paragraphs = [
        re.sub(r"\s+", " ", paragraph).strip() for paragraph in paragraphs
    ]
    return "\n\n".join(paragraph for paragraph in normalized_paragraphs if paragraph)


def normalize_document(document: ExtractedDocument) -> ExtractedDocument:
    normalized_blocks = [_normalize_block(block) for block in document.blocks]
    repeated_keys = _repeated_page_furniture(normalized_blocks)
    warnings = list(document.warnings)

    retained: list[ExtractedBlock] = []
    for block in normalized_blocks:
        furniture_key = _page_furniture_key(block)
        if furniture_key is not None and furniture_key in repeated_keys:
            warnings.append(
                ExtractionWarning(
                    code=f"repeated_{furniture_key[0]}_removed",
                    message=f"Removed a repeated page {furniture_key[0]}.",
                    page_number=block.start_page,
                )
            )
            continue
        retained.append(block)

    joined = _join_cross_page_hyphenation(retained)
    ordered = _assign_offsets_and_order(joined)
    if not ordered:
        raise ExtractionError("Normalization produced no searchable text")
    return ExtractedDocument(
        blocks=ordered,
        page_count=document.page_count,
        warnings=warnings,
        parser_version=document.parser_version,
        source_kind=document.source_kind,
        language=document.language,
        metadata=dict(document.metadata),
    )


def _normalize_block(block: ExtractedBlock) -> ExtractedBlock:
    normalized_text = normalize_text(block.text, block.block_type)
    if block.text.strip() and not normalized_text:
        raise ExtractionError(
            f"Normalization removed non-empty source block at order {block.order}"
        )
    return replace(
        block,
        text=normalized_text,
        heading_path=[
            normalize_text(heading, "heading") for heading in block.heading_path if heading.strip()
        ],
        metadata=dict(block.metadata),
    )


def _page_furniture_key(block: ExtractedBlock) -> tuple[str, str] | None:
    position = block.metadata.get("page_position")
    if position not in _REPEATED_PAGE_POSITIONS or block.start_page is None:
        return None
    if block.start_page != block.end_page or not block.text or len(block.text) > 160:
        return None
    return str(position), block.text.casefold()


def _repeated_page_furniture(
    blocks: list[ExtractedBlock],
) -> set[tuple[str, str]]:
    pages_by_key: defaultdict[tuple[str, str], set[int]] = defaultdict(set)
    page_numbers = {block.start_page for block in blocks if block.start_page is not None}
    if len(page_numbers) < 2:
        return set()
    for block in blocks:
        key = _page_furniture_key(block)
        if key is not None and block.start_page is not None:
            pages_by_key[key].add(block.start_page)
    minimum_repetitions = max(2, (len(page_numbers) + 1) // 2)
    return {
        key
        for key, pages in pages_by_key.items()
        if len(pages) >= minimum_repetitions
    }


def _join_cross_page_hyphenation(blocks: list[ExtractedBlock]) -> list[ExtractedBlock]:
    joined: list[ExtractedBlock] = []
    for block in blocks:
        if joined and _can_join_hyphenated(joined[-1], block):
            previous = joined[-1]
            metadata = dict(previous.metadata)
            source_orders = list(metadata.get("source_orders", [previous.order]))
            source_orders.extend(block.metadata.get("source_orders", [block.order]))
            metadata["source_orders"] = source_orders
            joined[-1] = replace(
                previous,
                text=f"{previous.text[:-1]}{block.text.lstrip()}",
                end_page=block.end_page,
                metadata=metadata,
            )
        else:
            joined.append(block)
    return joined


def _can_join_hyphenated(previous: ExtractedBlock, current: ExtractedBlock) -> bool:
    return (
        previous.block_type == current.block_type == "paragraph"
        and previous.end_page is not None
        and current.start_page == previous.end_page + 1
        and previous.heading_path == current.heading_path
        and bool(re.search(r"\w-$", previous.text))
        and bool(re.match(r"^\w", current.text))
    )


def _assign_offsets_and_order(blocks: list[ExtractedBlock]) -> list[ExtractedBlock]:
    ordered: list[ExtractedBlock] = []
    cursor = 0
    for order, block in enumerate(blocks):
        metadata = dict(block.metadata)
        metadata.setdefault("source_orders", [block.order])
        metadata["start_offset"] = cursor
        metadata["end_offset"] = cursor + len(block.text)
        ordered.append(replace(block, order=order, metadata=metadata))
        cursor += len(block.text) + 2
    return ordered

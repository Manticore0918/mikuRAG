from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExtractionWarning:
    code: str
    message: str
    page_number: int | None = None


@dataclass(frozen=True)
class ExtractedBlock:
    text: str
    block_type: str
    order: int
    start_page: int | None = None
    end_page: int | None = None
    heading_level: int | None = None
    heading_path: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractedSection:
    """Compatibility view used by the legacy character chunker."""

    text: str
    locator: dict[str, Any]


@dataclass(frozen=True)
class ExtractedDocument:
    blocks: list[ExtractedBlock]
    page_count: int | None
    warnings: list[ExtractionWarning] = field(default_factory=list)

    @property
    def sections(self) -> list[ExtractedSection]:
        """Group structural blocks into the legacy page/section boundaries."""
        grouped: list[ExtractedSection] = []
        group_key: tuple[str, Any] | None = None
        group_texts: list[str] = []
        group_locator: dict[str, Any] = {}

        def flush() -> None:
            if not group_texts:
                return
            grouped.append(
                ExtractedSection(
                    text="\n\n".join(group_texts),
                    locator=dict(group_locator),
                )
            )

        for block in self.blocks:
            locator = _block_locator(block)
            if "page" in locator:
                key = ("page", locator["page"])
            elif "section" in locator:
                key = ("section", locator["section"])
            else:
                key = ("block", block.order)

            if group_key is not None and key != group_key:
                flush()
                group_texts = []
                group_locator = {}
            if not group_texts:
                group_key = key
                group_locator = locator
            else:
                _extend_page_range(group_locator, locator)
            group_texts.append(block.text)

        flush()
        return grouped


def _block_locator(block: ExtractedBlock) -> dict[str, Any]:
    locator = dict(block.metadata.get("locator", {}))
    if block.start_page is not None:
        locator["start_page"] = block.start_page
    if block.end_page is not None:
        locator["end_page"] = block.end_page
    if block.start_page is not None:
        if block.start_page == block.end_page:
            locator["page"] = block.start_page
        else:
            locator.pop("page", None)
    if block.heading_path:
        locator["heading_path"] = list(block.heading_path)
    return locator


def _extend_page_range(target: dict[str, Any], source: dict[str, Any]) -> None:
    source_start = source.get("start_page")
    source_end = source.get("end_page")
    if isinstance(source_start, int):
        current_start = target.get("start_page")
        target["start_page"] = (
            min(current_start, source_start) if isinstance(current_start, int) else source_start
        )
    if isinstance(source_end, int):
        current_end = target.get("end_page")
        target["end_page"] = (
            max(current_end, source_end) if isinstance(current_end, int) else source_end
        )
    if target.get("start_page") != target.get("end_page"):
        target.pop("page", None)

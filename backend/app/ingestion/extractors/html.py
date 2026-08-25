import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from app.ingestion.contracts import ExtractedBlock, ExtractedDocument, ExtractionWarning
from app.ingestion.errors import ExtractionError
from app.ingestion.extractors.registry import ExtractionContext

_BLOCK_TAGS = {"blockquote", "dd", "dt", "li", "p", "pre", "tr"}
_HEADING_TAGS = {f"h{level}" for level in range(1, 7)}
_IGNORED_TAGS = {"aside", "footer", "nav", "noscript", "script", "style", "svg", "template"}
_NOISE_ROLES = {"banner", "contentinfo", "navigation"}
_NOISE_HINT = re.compile(
    r"(?:^|[-_\s])(breadcrumb|cookie|footer|menu|nav|newsletter|sidebar|social)(?:$|[-_\s])",
    re.IGNORECASE,
)
_CSS_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_MAX_DOM_DEPTH = 256


@dataclass
class _Frame:
    tag: str
    selector: str
    hidden: bool
    child_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class _BlockDraft:
    tag: str
    selector: str
    line_start: int
    text_start: int | None = None
    text_end: int | None = None
    pieces: list[str] = field(default_factory=list)


class _SemanticHTMLParser(HTMLParser):
    def __init__(self, content: str) -> None:
        super().__init__(convert_charrefs=True)
        self.content = content
        self.blocks: list[ExtractedBlock] = []
        self.heading_path: list[str] = []
        self.title_pieces: list[str] = []
        self.canonical_uri: str | None = None
        self.noise_elements = 0
        self._frames: list[_Frame] = []
        self._active: _BlockDraft | None = None
        self._search_cursor = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attributes = {key.casefold(): value or "" for key, value in attrs}
        parent = self._frames[-1] if self._frames else None
        if parent is not None:
            count = parent.child_counts.get(tag, 0) + 1
            parent.child_counts[tag] = count
        else:
            count = 1
        selector = _selector(parent, tag, count, attributes.get("id"))
        own_noise = _is_noise(tag, attributes)
        hidden = (parent.hidden if parent is not None else False) or own_noise
        if own_noise:
            self.noise_elements += 1
        self._frames.append(_Frame(tag=tag, selector=selector, hidden=hidden))
        if len(self._frames) > _MAX_DOM_DEPTH:
            raise ExtractionError("The HTML Document exceeds the safe nesting limit")

        if tag == "title":
            self._in_title = True
        if tag == "link" and not hidden:
            rel = {item.casefold() for item in attributes.get("rel", "").split()}
            href = attributes.get("href", "").strip()
            if "canonical" in rel and _safe_web_uri(href):
                self.canonical_uri = href
        if hidden:
            return
        if self._active is not None and tag in (_BLOCK_TAGS | _HEADING_TAGS):
            self._flush_active()
        if tag in (_BLOCK_TAGS | _HEADING_TAGS):
            self._active = _BlockDraft(
                tag=tag,
                selector=selector,
                line_start=self.getpos()[0],
            )
        elif tag in {"br", "hr"} and self._active is not None:
            self._active.pieces.append("\n")
        elif tag in {"td", "th"} and self._active is not None and self._active.tag == "tr":
            if self._active.pieces:
                self._active.pieces.append(" | ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self._active is not None and self._active.tag == tag:
            self._flush_active()
        if tag == "title":
            self._in_title = False
        for index in range(len(self._frames) - 1, -1, -1):
            if self._frames[index].tag == tag:
                del self._frames[index:]
                break

    def handle_data(self, data: str) -> None:
        frame = self._frames[-1] if self._frames else None
        if not data or (frame is not None and frame.hidden):
            return
        if self._in_title:
            self.title_pieces.append(data)
            return
        if not data.strip():
            if self._active is not None and self._active.tag == "pre":
                self._active.pieces.append(data)
            return
        if self._active is None:
            self._active = _BlockDraft(
                tag="text",
                selector=frame.selector if frame is not None else "html",
                line_start=self.getpos()[0],
            )
        start = self.content.find(data, self._search_cursor)
        if start < 0:
            start = self.content.find(data)
        if start >= 0:
            self._search_cursor = start + len(data)
            if self._active.text_start is None:
                self._active.text_start = start
            self._active.text_end = start + len(data)
        self._active.pieces.append(data)

    def close(self) -> None:
        super().close()
        self._flush_active()

    def _flush_active(self) -> None:
        draft = self._active
        self._active = None
        if draft is None:
            return
        if draft.tag == "pre":
            text = "".join(draft.pieces).strip("\n")
        else:
            text = re.sub(r"\s+", " ", " ".join(draft.pieces)).strip()
        if not text:
            return
        heading_level = None
        block_type = "paragraph"
        if draft.tag in _HEADING_TAGS:
            heading_level = int(draft.tag[1])
            self.heading_path = [*self.heading_path[: heading_level - 1], text]
            block_type = "heading"
        elif draft.tag == "li":
            block_type = "list_item"
        elif draft.tag == "pre":
            block_type = "code"
        elif draft.tag == "tr":
            block_type = "table"
        locator: dict[str, object] = {
            "element": draft.selector,
            "line_start": draft.line_start,
            "line_end": max(draft.line_start, self.getpos()[0]),
        }
        if draft.text_start is not None:
            locator["text_start"] = draft.text_start
        if draft.text_end is not None:
            locator["text_end"] = draft.text_end
        self.blocks.append(
            ExtractedBlock(
                text=text,
                block_type=block_type,
                order=len(self.blocks),
                heading_level=heading_level,
                heading_path=list(self.heading_path),
                metadata={"locator": locator},
            )
        )


def extract_html(context: ExtractionContext) -> ExtractedDocument:
    content = _read_utf8(context.path, "HTML")
    try:
        parser = _SemanticHTMLParser(content)
        parser.feed(content)
        parser.close()
    except ExtractionError:
        raise
    except Exception as error:
        raise ExtractionError("The HTML Document could not be parsed safely") from error

    blocks = parser.blocks
    title = re.sub(r"\s+", " ", " ".join(parser.title_pieces)).strip()
    if title:
        title_block = ExtractedBlock(
            text=title,
            block_type="heading",
            order=0,
            heading_level=1,
            heading_path=[title],
            metadata={"locator": {"element": "title"}},
        )
        blocks = [title_block, *[
            ExtractedBlock(
                text=block.text,
                block_type=block.block_type,
                order=index,
                start_page=block.start_page,
                end_page=block.end_page,
                heading_level=block.heading_level,
                heading_path=block.heading_path,
                metadata=block.metadata,
            )
            for index, block in enumerate(blocks, start=1)
        ]]
    if not any(block.text.strip() for block in blocks):
        raise ExtractionError("No extractable content was found in the HTML Document")
    warnings = []
    if parser.noise_elements:
        warnings.append(
            ExtractionWarning(
                code="html_navigation_removed",
                message="Navigation or other non-content HTML elements were removed.",
            )
        )
    metadata: dict[str, object] = {}
    if title:
        metadata["title"] = title[:2_000]
    canonical_uri = context.source_uri or parser.canonical_uri
    if canonical_uri:
        metadata["canonical_uri"] = canonical_uri
    return ExtractedDocument(
        blocks=blocks,
        page_count=None,
        warnings=warnings,
        metadata=metadata,
    )


def _read_utf8(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise ExtractionError(f"The {label} Document could not be read as UTF-8") from error


def _selector(parent: _Frame | None, tag: str, count: int, element_id: str | None) -> str:
    if element_id and _CSS_ID.fullmatch(element_id):
        component = f"#{element_id}"
    else:
        component = f"{tag}:nth-of-type({count})"
    return component if parent is None else f"{parent.selector} > {component}"


def _is_noise(tag: str, attributes: dict[str, str]) -> bool:
    if (
        tag in _IGNORED_TAGS
        or "hidden" in attributes
        or attributes.get("aria-hidden", "").casefold() == "true"
    ):
        return True
    if attributes.get("role", "").casefold() in _NOISE_ROLES:
        return True
    hints = f"{attributes.get('id', '')} {attributes.get('class', '')}"
    return bool(_NOISE_HINT.search(hints))


def _safe_web_uri(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


__all__ = ["extract_html"]

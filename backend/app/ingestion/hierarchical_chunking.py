import hashlib
import json
import re
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, replace

from app.ingestion.contracts import ExtractedBlock, ExtractedDocument
from app.ingestion.tokenization import ConservativeTokenizer, Tokenizer

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。！？])\s*")


@dataclass(frozen=True)
class HierarchicalChunkingConfig:
    child_min_tokens: int = 200
    child_target_tokens: int = 500
    child_max_tokens: int = 750
    child_overlap_tokens: int = 60
    parent_target_tokens: int = 2_000
    parent_max_tokens: int = 3_000
    chunking_version: str = "hierarchical_v1"

    def __post_init__(self) -> None:
        if not 0 < self.child_min_tokens <= self.child_target_tokens:
            raise ValueError("child_min_tokens must be positive and no greater than target")
        if self.child_target_tokens > self.child_max_tokens:
            raise ValueError("child_target_tokens cannot exceed child_max_tokens")
        if not 0 <= self.child_overlap_tokens < self.child_min_tokens:
            raise ValueError("child_overlap_tokens must be smaller than child_min_tokens")
        if self.parent_target_tokens <= self.child_max_tokens:
            raise ValueError("parent_target_tokens must exceed child_max_tokens")
        if self.parent_target_tokens > self.parent_max_tokens:
            raise ValueError("parent_target_tokens cannot exceed parent_max_tokens")
        if not self.chunking_version.strip():
            raise ValueError("chunking_version cannot be empty")


@dataclass(frozen=True)
class ConstructedChunk:
    text: str
    embedding_text: str | None
    chunk_level: str
    ordinal: int
    parent_ordinal: int | None
    start_page: int | None
    end_page: int | None
    start_offset: int | None
    end_offset: int | None
    heading_path: list[str]
    content_type: str
    token_count: int
    chunking_version: str
    content_hash: str
    locator: dict[str, object]
    source_block_orders: tuple[int, ...]


@dataclass(frozen=True)
class ConstructedHierarchy:
    parents: list[ConstructedChunk]
    children: list[ConstructedChunk]
    tokenizer_name: str


@dataclass(frozen=True)
class _Segment:
    text: str
    block_type: str
    source_order: int
    start_page: int | None
    end_page: int | None
    start_offset: int | None
    end_offset: int | None
    heading_path: list[str]
    locator: dict[str, object]


@dataclass(frozen=True)
class _ParentDraft:
    segments: list[_Segment]


def construct_hierarchy(
    document: ExtractedDocument,
    *,
    config: HierarchicalChunkingConfig | None = None,
    tokenizer: Tokenizer | None = None,
) -> ConstructedHierarchy:
    active_config = config or HierarchicalChunkingConfig()
    active_tokenizer = tokenizer or ConservativeTokenizer()
    source_segments = _segments_for_blocks(
        document.blocks,
        active_config.parent_max_tokens,
        active_tokenizer,
    )
    parent_drafts = _group_parents(source_segments, active_config, active_tokenizer)

    parents: list[ConstructedChunk] = []
    children: list[ConstructedChunk] = []
    for parent_ordinal, draft in enumerate(parent_drafts):
        parents.append(
            _constructed_chunk(
                draft.segments,
                chunk_level="parent",
                ordinal=parent_ordinal,
                parent_ordinal=None,
                config=active_config,
                tokenizer=active_tokenizer,
                embedding_text=None,
            )
        )
        child_groups = _group_children(draft.segments, active_config, active_tokenizer)
        for child_group in child_groups:
            text = _join_segments(child_group)
            heading_path = _heading_path(child_group)
            prefix = _bounded_heading_prefix(
                heading_path,
                active_config.child_max_tokens,
                active_tokenizer,
            )
            enriched = f"{prefix}\n\n{text}" if prefix else text
            children.append(
                _constructed_chunk(
                    child_group,
                    chunk_level="child",
                    ordinal=len(children),
                    parent_ordinal=parent_ordinal,
                    config=active_config,
                    tokenizer=active_tokenizer,
                    embedding_text=enriched,
                )
            )

    return ConstructedHierarchy(
        parents=parents,
        children=children,
        tokenizer_name=active_tokenizer.name,
    )


def _segments_for_blocks(
    blocks: list[ExtractedBlock],
    max_tokens: int,
    tokenizer: Tokenizer,
) -> list[_Segment]:
    segments: list[_Segment] = []
    for block in blocks:
        segment = _segment_from_block(block)
        segments.extend(_split_segment(segment, max_tokens, tokenizer, repeat_table_header=True))
    return segments


def _segment_from_block(block: ExtractedBlock) -> _Segment:
    start_offset = _metadata_int(block, "start_offset")
    end_offset = _metadata_int(block, "end_offset")
    return _Segment(
        text=block.text,
        block_type=block.block_type,
        source_order=block.order,
        start_page=block.start_page,
        end_page=block.end_page,
        start_offset=start_offset,
        end_offset=end_offset,
        heading_path=list(block.heading_path),
        locator=dict(block.metadata.get("locator", {})),
    )


def _metadata_int(block: ExtractedBlock, key: str) -> int | None:
    value = block.metadata.get(key)
    return value if type(value) is int and value >= 0 else None


def _split_segment(
    segment: _Segment,
    max_tokens: int,
    tokenizer: Tokenizer,
    *,
    repeat_table_header: bool,
) -> list[_Segment]:
    if tokenizer.count(segment.text) <= max_tokens:
        return [segment]
    if segment.block_type == "table" and repeat_table_header:
        table_parts = _split_table(segment, max_tokens, tokenizer)
        if len(table_parts) > 1:
            return table_parts
    if segment.block_type in {"paragraph", "list", "list_item"}:
        sentence_parts = _split_sentences(segment, max_tokens, tokenizer)
        if len(sentence_parts) > 1:
            return sentence_parts
    return _hard_split_segment(segment, max_tokens, tokenizer)


def _split_sentences(
    segment: _Segment,
    max_tokens: int,
    tokenizer: Tokenizer,
) -> list[_Segment]:
    sentences = [
        sentence.strip()
        for sentence in _SENTENCE_BOUNDARY.split(segment.text)
        if sentence.strip()
    ]
    if len(sentences) <= 1:
        return _hard_split_segment(segment, max_tokens, tokenizer)

    pieces: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        if tokenizer.count(sentence) > max_tokens:
            if current:
                pieces.append(" ".join(current))
                current = []
            pieces.extend(tokenizer.split(sentence, max_tokens))
            continue
        proposed = " ".join([*current, sentence])
        if current and tokenizer.count(proposed) > max_tokens:
            pieces.append(" ".join(current))
            current = [sentence]
        else:
            current.append(sentence)
    if current:
        pieces.append(" ".join(current))
    return _segments_for_text_pieces(segment, pieces)


def _split_table(
    segment: _Segment,
    max_tokens: int,
    tokenizer: Tokenizer,
) -> list[_Segment]:
    rows = [row for row in segment.text.splitlines() if row.strip()]
    if len(rows) <= 2:
        return _hard_split_segment(segment, max_tokens, tokenizer)
    header = rows[0]
    if tokenizer.count(header) >= max_tokens:
        return _hard_split_segment(segment, max_tokens, tokenizer)
    pieces: list[str] = []
    current = [header]
    for row in rows[1:]:
        proposed = "\n".join([*current, row])
        if len(current) > 1 and tokenizer.count(proposed) > max_tokens:
            pieces.append("\n".join(current))
            current = [header, row]
        elif tokenizer.count(proposed) > max_tokens:
            row_parts = tokenizer.split(row, max(1, max_tokens - tokenizer.count(header)))
            pieces.extend(f"{header}\n{part}" for part in row_parts)
            current = [header]
        else:
            current.append(row)
    if len(current) > 1:
        pieces.append("\n".join(current))
    return [replace(segment, text=piece) for piece in pieces]


def _hard_split_segment(
    segment: _Segment,
    max_tokens: int,
    tokenizer: Tokenizer,
) -> list[_Segment]:
    return _segments_for_text_pieces(segment, tokenizer.split(segment.text, max_tokens))


def _segments_for_text_pieces(
    source: _Segment,
    pieces: list[str],
) -> list[_Segment]:
    segments: list[_Segment] = []
    cursor = 0
    for piece in pieces:
        relative_start = source.text.find(piece, cursor)
        if relative_start < 0:
            relative_start = cursor
        relative_end = relative_start + len(piece)
        start_offset = (
            source.start_offset + relative_start if source.start_offset is not None else None
        )
        end_offset = (
            source.start_offset + relative_end if source.start_offset is not None else None
        )
        segments.append(
            replace(
                source,
                text=piece,
                start_offset=start_offset,
                end_offset=end_offset,
                locator=_slice_locator(source, relative_start, relative_end),
            )
        )
        cursor = relative_end
    return segments


def _group_parents(
    segments: list[_Segment],
    config: HierarchicalChunkingConfig,
    tokenizer: Tokenizer,
) -> list[_ParentDraft]:
    queue = deque(segments)
    parents: list[_ParentDraft] = []
    current: list[_Segment] = []
    heading_needs_body = False

    def flush() -> None:
        nonlocal current, heading_needs_body
        if current:
            parents.append(_ParentDraft(segments=current))
        current = []
        heading_needs_body = False

    while queue:
        segment = queue.popleft()
        if segment.block_type == "heading":
            flush()
            current = [segment]
            heading_needs_body = True
            continue

        current_tokens = tokenizer.count(_join_segments(current))
        if (
            current
            and not heading_needs_body
            and current_tokens >= config.parent_target_tokens
        ):
            flush()
            queue.appendleft(segment)
            continue

        proposed = [*current, segment]
        if current and tokenizer.count(_join_segments(proposed)) > config.parent_max_tokens:
            if heading_needs_body:
                available = config.parent_max_tokens - current_tokens
                if available <= 0:
                    flush()
                    queue.appendleft(segment)
                    continue
                parts = _split_segment(
                    segment,
                    available,
                    tokenizer,
                    repeat_table_header=True,
                )
                current.append(parts[0])
                queue.extendleft(reversed(parts[1:]))
                flush()
            else:
                flush()
                queue.appendleft(segment)
            continue

        current.append(segment)
        heading_needs_body = False

    flush()
    return parents


def _group_children(
    parent_segments: list[_Segment],
    config: HierarchicalChunkingConfig,
    tokenizer: Tokenizer,
) -> list[list[_Segment]]:
    heading_path = _heading_path(parent_segments)
    prefix = _bounded_heading_prefix(
        heading_path,
        config.child_max_tokens,
        tokenizer,
    )
    prefix_tokens = tokenizer.count(f"{prefix}\n\n") if prefix else 0
    content_max = max(1, config.child_max_tokens - prefix_tokens)
    content_target = max(1, min(config.child_target_tokens, content_max))
    content_min = max(1, min(config.child_min_tokens, content_target))

    expanded: list[_Segment] = []
    for segment in parent_segments:
        expanded.extend(
            _split_segment(
                segment,
                content_max,
                tokenizer,
                repeat_table_header=True,
            )
        )
    base_groups = _pack_child_groups(
        expanded,
        content_min=content_min,
        content_target=content_target,
        content_max=content_max,
        tokenizer=tokenizer,
    )
    return _apply_overlap(
        base_groups,
        overlap_tokens=config.child_overlap_tokens,
        content_max=content_max,
        tokenizer=tokenizer,
    )


def _pack_child_groups(
    segments: list[_Segment],
    *,
    content_min: int,
    content_target: int,
    content_max: int,
    tokenizer: Tokenizer,
) -> list[list[_Segment]]:
    queue = deque(segments)
    groups: list[list[_Segment]] = []
    current: list[_Segment] = []
    unprocessed_tokens = sum(tokenizer.count(segment.text) for segment in segments)

    def flush() -> None:
        nonlocal current
        if current:
            groups.append(current)
        current = []

    while queue:
        segment = queue.popleft()
        segment_tokens = tokenizer.count(segment.text)
        unprocessed_tokens -= segment_tokens
        current_tokens = tokenizer.count(_join_segments(current))
        remaining_tokens = segment_tokens + unprocessed_tokens
        if current and current_tokens >= content_target:
            can_absorb_small_tail = (
                remaining_tokens < content_min
                and current_tokens + remaining_tokens <= content_max
            )
            if not can_absorb_small_tail:
                flush()

        proposed = [*current, segment]
        if current and tokenizer.count(_join_segments(proposed)) > content_max:
            if all(item.block_type == "heading" for item in current):
                available = content_max - current_tokens
                parts = _split_segment(
                    segment,
                    max(1, available),
                    tokenizer,
                    repeat_table_header=True,
                )
                current.append(parts[0])
                queue.extendleft(reversed(parts[1:]))
                unprocessed_tokens += sum(
                    tokenizer.count(part.text) for part in parts[1:]
                )
                flush()
            else:
                flush()
                current.append(segment)
        else:
            current.append(segment)

    flush()
    if len(groups) >= 2:
        last_tokens = tokenizer.count(_join_segments(groups[-1]))
        merged = [*groups[-2], *groups[-1]]
        if last_tokens < content_min and tokenizer.count(_join_segments(merged)) <= content_max:
            groups[-2] = merged
            groups.pop()
    return groups


def _apply_overlap(
    groups: list[list[_Segment]],
    *,
    overlap_tokens: int,
    content_max: int,
    tokenizer: Tokenizer,
) -> list[list[_Segment]]:
    if overlap_tokens <= 0:
        return groups
    overlapped: list[list[_Segment]] = []
    for index, group in enumerate(groups):
        if index == 0:
            overlapped.append(group)
            continue
        if all(segment.block_type in {"table", "code"} for segment in group):
            overlapped.append(group)
            continue
        group_tokens = tokenizer.count(_join_segments(group))
        available = min(overlap_tokens, max(0, content_max - group_tokens))
        prefix = _tail_segments(groups[index - 1], available, tokenizer)
        while prefix and tokenizer.count(_join_segments([*prefix, *group])) > content_max:
            available -= 1
            prefix = _tail_segments(groups[index - 1], available, tokenizer)
        overlapped.append([*prefix, *group])
    return overlapped


def _tail_segments(
    segments: list[_Segment],
    max_tokens: int,
    tokenizer: Tokenizer,
) -> list[_Segment]:
    if max_tokens <= 0:
        return []
    selected: list[_Segment] = []
    remaining = max_tokens
    for segment in reversed(segments):
        segment_tokens = tokenizer.count(segment.text)
        if segment_tokens <= remaining:
            selected.append(segment)
            remaining -= segment_tokens
        else:
            tail = tokenizer.tail(segment.text, remaining)
            if tail:
                relative_start = segment.text.rfind(tail)
                start_offset = (
                    segment.start_offset + relative_start
                    if segment.start_offset is not None and relative_start >= 0
                    else segment.start_offset
                )
                selected.append(replace(segment, text=tail, start_offset=start_offset))
            remaining = 0
        if remaining <= 0:
            break
    selected.reverse()
    return selected


def _constructed_chunk(
    segments: list[_Segment],
    *,
    chunk_level: str,
    ordinal: int,
    parent_ordinal: int | None,
    config: HierarchicalChunkingConfig,
    tokenizer: Tokenizer,
    embedding_text: str | None,
) -> ConstructedChunk:
    text = _join_segments(segments)
    heading_path = _heading_path(segments)
    start_page = _minimum_optional(segment.start_page for segment in segments)
    end_page = _maximum_optional(segment.end_page for segment in segments)
    start_offset = _minimum_optional(segment.start_offset for segment in segments)
    end_offset = _maximum_optional(segment.end_offset for segment in segments)
    content_type = _content_type(segments, chunk_level)
    locator = _merged_locator(segments)
    if start_page is not None:
        locator["start_page"] = start_page
    if end_page is not None:
        locator["end_page"] = end_page
    if start_page is not None and start_page == end_page:
        locator["page"] = start_page
    if heading_path:
        locator["heading_path"] = list(heading_path)
    content_hash = _content_hash(
        text=text,
        chunk_level=chunk_level,
        heading_path=heading_path,
        content_type=content_type,
        chunking_version=config.chunking_version,
    )
    return ConstructedChunk(
        text=text,
        embedding_text=embedding_text,
        chunk_level=chunk_level,
        ordinal=ordinal,
        parent_ordinal=parent_ordinal,
        start_page=start_page,
        end_page=end_page,
        start_offset=start_offset,
        end_offset=end_offset,
        heading_path=heading_path,
        content_type=content_type,
        token_count=tokenizer.count(text),
        chunking_version=config.chunking_version,
        content_hash=content_hash,
        locator=locator,
        source_block_orders=tuple(dict.fromkeys(segment.source_order for segment in segments)),
    )


def _slice_locator(source: _Segment, relative_start: int, relative_end: int) -> dict[str, object]:
    locator = dict(source.locator)
    line_start = locator.get("line_start")
    if type(line_start) is int and line_start > 0:
        slice_start_line = line_start + source.text[:relative_start].count("\n")
        slice_end_line = slice_start_line + source.text[relative_start:relative_end].count("\n")
        locator["line_start"] = slice_start_line
        locator["line_end"] = slice_end_line
    text_start = locator.get("text_start")
    if type(text_start) is int and text_start >= 0:
        locator["text_start"] = text_start + relative_start
        locator["text_end"] = text_start + relative_end
    return locator


def _merged_locator(segments: list[_Segment]) -> dict[str, object]:
    locator: dict[str, object] = {}
    stable_keys = (
        "language",
        "module",
        "path",
        "section",
        "source_kind",
        "source_path",
        "source_uri",
        "symbol",
    )
    for key in stable_keys:
        values = [
            segment.locator.get(key)
            for segment in segments
            if segment.locator.get(key) is not None
        ]
        if values and all(value == values[0] for value in values):
            locator[key] = values[0]

    elements = [
        value
        for segment in segments
        if isinstance((value := segment.locator.get("element")), str) and value
    ]
    if elements:
        if all(element == elements[0] for element in elements):
            locator["element"] = elements[0]
        else:
            locator["element_start"] = elements[0]
            locator["element_end"] = elements[-1]

    _merge_integer_range(locator, segments, "line_start", "line_end", positive=True)
    _merge_integer_range(locator, segments, "text_start", "text_end", positive=False)
    return locator


def _merge_integer_range(
    locator: dict[str, object],
    segments: list[_Segment],
    start_key: str,
    end_key: str,
    *,
    positive: bool,
) -> None:
    minimum = 1 if positive else 0
    starts = [
        value
        for segment in segments
        if type(value := segment.locator.get(start_key)) is int and value >= minimum
    ]
    ends = [
        value
        for segment in segments
        if type(value := segment.locator.get(end_key)) is int and value >= minimum
    ]
    if starts:
        locator[start_key] = min(starts)
    if ends:
        locator[end_key] = max(ends)


def _join_segments(segments: list[_Segment]) -> str:
    if not segments:
        return ""
    parts = [segments[0].text]
    for previous, current in zip(segments, segments[1:], strict=False):
        separator = " " if previous.source_order == current.source_order else "\n\n"
        parts.append(f"{separator}{current.text}")
    return "".join(parts).strip()


def _heading_path(segments: list[_Segment]) -> list[str]:
    paths = [segment.heading_path for segment in segments if segment.heading_path]
    return list(max(paths, key=len)) if paths else []


def _heading_prefix(heading_path: list[str]) -> str:
    return " > ".join(heading_path)


def _bounded_heading_prefix(
    heading_path: list[str],
    child_max_tokens: int,
    tokenizer: Tokenizer,
) -> str:
    prefix = _heading_prefix(heading_path)
    if not prefix:
        return ""
    available = max(1, child_max_tokens - 1)
    return prefix if tokenizer.count(prefix) <= available else tokenizer.tail(prefix, available)


def _content_type(segments: list[_Segment], chunk_level: str) -> str:
    if chunk_level.endswith("summary"):
        return "summary"
    mapped = {
        "paragraph": "paragraph",
        "heading": "paragraph",
        "list": "list",
        "list_item": "list",
        "table": "table",
        "code": "code",
        "preformatted": "code",
    }
    types = {mapped.get(segment.block_type, "mixed") for segment in segments}
    return next(iter(types)) if len(types) == 1 else "mixed"


def _content_hash(
    *,
    text: str,
    chunk_level: str,
    heading_path: list[str],
    content_type: str,
    chunking_version: str,
) -> str:
    payload = json.dumps(
        {
            "text": text,
            "chunk_level": chunk_level,
            "heading_path": heading_path,
            "content_type": content_type,
            "chunking_version": chunking_version,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _minimum_optional(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _maximum_optional(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None

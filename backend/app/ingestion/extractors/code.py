import ast
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.ingestion.contracts import ExtractedBlock, ExtractedDocument, ExtractionWarning
from app.ingestion.errors import ExtractionError
from app.ingestion.extractors.registry import ExtractionContext

_JS_DECLARATION = re.compile(
    r"^\s*(?:(?:export|declare|default|async|abstract)\s+)*"
    r"(?:(?:class|interface|type|enum|namespace|function)\s+([A-Za-z_$][\w$]*)"
    r"|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=)",
)


@dataclass(frozen=True)
class _SourceRange:
    start: int
    end: int
    symbol: str | None


def extract_python(context: ExtractionContext) -> ExtractedDocument:
    content = _read_source(context.path)
    try:
        tree = ast.parse(content, filename=context.source_path or context.path.name)
    except SyntaxError as error:
        location = f" near line {error.lineno}" if error.lineno else ""
        raise ExtractionError(f"The Python source could not be parsed safely{location}") from error
    lines = content.splitlines()
    ranges = _python_ranges(tree, len(lines))
    return _code_document(
        lines,
        ranges,
        language="python",
        source_path=context.source_path or context.path.name,
    )


def extract_javascript(context: ExtractionContext) -> ExtractedDocument:
    content = _read_source(context.path)
    lines = content.splitlines()
    declarations: list[tuple[int, str]] = []
    for line_number, line in enumerate(lines, start=1):
        match = _JS_DECLARATION.match(line)
        if match:
            declarations.append((line_number, match.group(1) or match.group(2)))
    ranges = _declaration_ranges(declarations, len(lines))
    warnings = []
    if not declarations:
        warnings.append(
            ExtractionWarning(
                code="code_symbol_index_fallback",
                message="No top-level symbols were detected; line-aware source blocks were used.",
            )
        )
    language = context.language or (
        "typescript" if context.media_type in {"text/typescript", "text/tsx"} else "javascript"
    )
    return _code_document(
        lines,
        ranges,
        language=language,
        source_path=context.source_path or context.path.name,
        warnings=warnings,
    )


def _python_ranges(tree: ast.Module, line_count: int) -> list[_SourceRange]:
    declarations: list[tuple[int, int, str | None]] = []
    for node in tree.body:
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        symbol = (
            node.name
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            else None
        )
        declarations.append((start, end, symbol))
    if not declarations:
        return _fallback_ranges(line_count)

    ranges: list[_SourceRange] = []
    cursor = 1
    unnamed_start: int | None = None
    unnamed_end = 0
    for start, end, symbol in declarations:
        if start > cursor:
            ranges.append(_SourceRange(cursor, start - 1, None))
        if symbol is None:
            if unnamed_start is None:
                unnamed_start = start
            unnamed_end = end
        else:
            if unnamed_start is not None:
                ranges.append(_SourceRange(unnamed_start, unnamed_end, None))
                unnamed_start = None
            ranges.append(_SourceRange(start, end, symbol))
        cursor = max(cursor, end + 1)
    if unnamed_start is not None:
        ranges.append(_SourceRange(unnamed_start, unnamed_end, None))
    if cursor <= line_count:
        ranges.append(_SourceRange(cursor, line_count, None))
    return ranges


def _declaration_ranges(
    declarations: list[tuple[int, str]], line_count: int
) -> list[_SourceRange]:
    if not declarations:
        return _fallback_ranges(line_count)
    ranges: list[_SourceRange] = []
    if declarations[0][0] > 1:
        ranges.append(_SourceRange(1, declarations[0][0] - 1, None))
    for index, (start, symbol) in enumerate(declarations):
        end = declarations[index + 1][0] - 1 if index + 1 < len(declarations) else line_count
        ranges.append(_SourceRange(start, end, symbol))
    return ranges


def _fallback_ranges(line_count: int, block_lines: int = 120) -> list[_SourceRange]:
    return [
        _SourceRange(start, min(line_count, start + block_lines - 1), None)
        for start in range(1, line_count + 1, block_lines)
    ]


def _code_document(
    lines: list[str],
    ranges: list[_SourceRange],
    *,
    language: str,
    source_path: str,
    warnings: list[ExtractionWarning] | None = None,
) -> ExtractedDocument:
    normalized_path = str(PurePosixPath(source_path.replace("\\", "/")))
    module = _module_name(normalized_path)
    blocks: list[ExtractedBlock] = []
    for source_range in ranges:
        text = "\n".join(lines[source_range.start - 1 : source_range.end]).strip("\n")
        if not text.strip():
            continue
        locator: dict[str, object] = {
            "path": normalized_path,
            "language": language,
            "line_start": source_range.start,
            "line_end": source_range.end,
        }
        if module:
            locator["module"] = module
        if source_range.symbol:
            locator["symbol"] = source_range.symbol
        blocks.append(
            ExtractedBlock(
                text=text,
                block_type="code",
                order=len(blocks),
                heading_path=[source_range.symbol] if source_range.symbol else [],
                metadata={"locator": locator},
            )
        )
    if not blocks:
        raise ExtractionError("No extractable source code was found in the Document")
    return ExtractedDocument(
        blocks=blocks,
        page_count=None,
        warnings=list(warnings or []),
        metadata={"module": module} if module else {},
    )


def _read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise ExtractionError("The source code could not be read as UTF-8") from error


def _module_name(source_path: str) -> str:
    path = PurePosixPath(source_path)
    without_suffix = path.with_suffix("")
    parts = list(without_suffix.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(part for part in parts if part not in {".", ""})


__all__ = ["extract_javascript", "extract_python"]

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.ingestion.storage import (
    language_for_suffix,
    source_kind_for_suffix,
    validate_file_format,
)


@dataclass(frozen=True)
class EvaluationCorpusDocument:
    document_id: str
    passage_id: str
    path: Path
    relative_path: str
    original_name: str
    media_type: str
    source_kind: str
    language: str | None
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ExecutableEvaluationCase:
    case_id: str
    category: str
    query: str
    relevant_passage_ids: tuple[str, ...]
    required_passage_ids: tuple[str, ...]
    expected_citation_pages: tuple[int, ...]
    expected_answer_terms: tuple[str, ...]
    expects_supported_answer: bool


@dataclass(frozen=True)
class ExecutableEvaluationDataset:
    schema_version: int
    version: str
    description: str
    manifest_path: Path
    documents: tuple[EvaluationCorpusDocument, ...]
    cases: tuple[ExecutableEvaluationCase, ...]


def load_executable_dataset(path: Path) -> ExecutableEvaluationDataset:
    manifest_path = path.resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Executable evaluation manifests require schema_version 1")
    version = _required_string(payload, "version", "evaluation corpus")
    description = str(payload.get("description") or "").strip()
    root = manifest_path.parent

    raw_documents = payload.get("documents")
    if not isinstance(raw_documents, list) or not raw_documents:
        raise ValueError("The executable evaluation corpus requires documents")
    documents = tuple(_load_document(root, item) for item in raw_documents)
    _require_unique((item.document_id for item in documents), "Document IDs")
    _require_unique((item.passage_id for item in documents), "passage IDs")
    _require_unique((item.sha256 for item in documents), "Document contents")

    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("The executable evaluation corpus requires cases")
    cases = tuple(_load_case(item) for item in raw_cases)
    _require_unique((item.case_id for item in cases), "case IDs")

    passage_ids = {item.passage_id for item in documents}
    for case in cases:
        relevant = set(case.relevant_passage_ids)
        required = set(case.required_passage_ids)
        if not required <= relevant:
            raise ValueError(
                f"Evaluation case '{case.case_id}' requires passages that are not relevant"
            )
        unknown = relevant - passage_ids
        if unknown:
            raise ValueError(
                f"Evaluation case '{case.case_id}' references unknown passages: "
                f"{', '.join(sorted(unknown))}"
            )

    return ExecutableEvaluationDataset(
        schema_version=1,
        version=version,
        description=description,
        manifest_path=manifest_path,
        documents=documents,
        cases=cases,
    )


def _load_document(root: Path, raw: object) -> EvaluationCorpusDocument:
    if not isinstance(raw, dict):
        raise ValueError("Evaluation corpus documents must be objects")
    document_id = _required_string(raw, "document_id", "evaluation corpus Document")
    passage_id = _required_string(raw, "passage_id", f"Document '{document_id}'")
    relative_text = _required_string(raw, "path", f"Document '{document_id}'")
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Document '{document_id}' path must stay inside the corpus")
    source_path = (root / relative).resolve()
    if not source_path.is_relative_to(root) or not source_path.is_file():
        raise ValueError(f"Document '{document_id}' source file is missing")
    suffix = source_path.suffix.casefold()
    media_type = validate_file_format(source_path, suffix)
    content = source_path.read_bytes()
    return EvaluationCorpusDocument(
        document_id=document_id,
        passage_id=passage_id,
        path=source_path,
        relative_path=relative.as_posix(),
        original_name=str(raw.get("original_name") or source_path.name),
        media_type=media_type,
        source_kind=source_kind_for_suffix(suffix),
        language=language_for_suffix(suffix),
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )


def _load_case(raw: object) -> ExecutableEvaluationCase:
    if not isinstance(raw, dict):
        raise ValueError("Executable evaluation cases must be objects")
    case_id = _required_string(raw, "case_id", "evaluation case")
    category = _required_string(raw, "category", f"case '{case_id}'")
    query = _required_string(raw, "query", f"case '{case_id}'")
    relevant = _string_tuple(raw.get("relevant_passage_ids"), "relevant_passage_ids")
    required = _string_tuple(raw.get("required_passage_ids"), "required_passage_ids")
    pages = raw.get("expected_citation_pages", [])
    if not isinstance(pages, list) or any(
        not isinstance(page, int) or isinstance(page, bool) or page < 1 for page in pages
    ):
        raise ValueError(f"Case '{case_id}' expected Citation pages must be positive integers")
    answer_terms = _string_tuple(raw.get("expected_answer_terms", []), "expected_answer_terms")
    expects_supported = raw.get("expects_supported_answer")
    if not isinstance(expects_supported, bool):
        raise ValueError(f"Case '{case_id}' requires expects_supported_answer")
    if expects_supported and not relevant:
        raise ValueError(f"Supported case '{case_id}' requires relevant passages")
    return ExecutableEvaluationCase(
        case_id=case_id,
        category=category,
        query=query,
        relevant_passage_ids=relevant,
        required_passage_ids=required,
        expected_citation_pages=tuple(pages),
        expected_answer_terms=answer_terms,
        expects_supported_answer=expects_supported,
    )


def _required_string(payload: dict[str, object], key: str, owner: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"The {owner} requires {key}")
    return value.strip()


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{field_name} must be a list of non-empty strings")
    result = tuple(item.strip() for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{field_name} cannot contain duplicates")
    return result


def _require_unique(values: object, name: str) -> None:
    collected = list(values)
    if len(set(collected)) != len(collected):
        raise ValueError(f"Executable evaluation {name} must be unique")

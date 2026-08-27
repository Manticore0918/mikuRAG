import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ingestion.storage import (
    language_for_suffix,
    source_kind_for_suffix,
    validate_file_format,
)

_STABLE_ID = re.compile(r"^[a-z0-9][a-z0-9._:/#_-]{2,127}$")


@dataclass(frozen=True)
class EvaluationCorpusPassage:
    passage_id: str
    locator_id: str
    locator_match: dict[str, Any]


@dataclass(frozen=True)
class EvaluationCorpusDocument:
    document_id: str
    passages: tuple[EvaluationCorpusPassage, ...]
    path: Path
    relative_path: str
    original_name: str
    media_type: str
    source_kind: str
    language: str | None
    tags: tuple[str, ...]
    source_uri: str | None
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class EvaluationHistoryMessage:
    role: str
    content: str


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
    filters: dict[str, tuple[str, ...]]
    history: tuple[EvaluationHistoryMessage, ...] = ()
    split: str = "train"
    relevance_grades: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutableEvaluationDataset:
    schema_version: int
    version: str
    description: str
    license_id: str
    provenance: str
    contains_sensitive_data: bool
    review_status: str
    headline_eligible: bool
    manifest_path: Path
    documents: tuple[EvaluationCorpusDocument, ...]
    cases: tuple[ExecutableEvaluationCase, ...]


def load_executable_dataset(path: Path) -> ExecutableEvaluationDataset:
    manifest_path = path.resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema_version = payload.get("schema_version")
    if schema_version not in {1, 2}:
        raise ValueError("Executable evaluation manifests require schema_version 1 or 2")
    version = _required_string(payload, "version", "evaluation corpus")
    description = str(payload.get("description") or "").strip()
    root = manifest_path.parent
    license_id = _required_string(payload, "license", "evaluation corpus")
    provenance = _required_string(payload, "provenance", "evaluation corpus")
    contains_sensitive_data = payload.get("contains_sensitive_data")
    if contains_sensitive_data is not False:
        raise ValueError(
            "Executable evaluation corpora must declare contains_sensitive_data false"
        )
    license_file = _corpus_file(
        root,
        _required_string(payload, "license_file", "evaluation corpus"),
        owner="Evaluation corpus license",
    )
    if not license_file.read_text(encoding="utf-8").strip():
        raise ValueError("The evaluation corpus license file cannot be empty")
    review_status = str(payload.get("review_status") or "draft").strip()
    headline_eligible = payload.get("headline_eligible", False)
    if not isinstance(headline_eligible, bool):
        raise ValueError("headline_eligible must be a Boolean")
    if schema_version == 2 and review_status != "reviewed":
        raise ValueError("Schema version 2 gold sets must declare review_status reviewed")

    raw_documents = payload.get("documents")
    if not isinstance(raw_documents, list) or not raw_documents:
        raise ValueError("The executable evaluation corpus requires documents")
    documents = tuple(
        _load_document(root, item, schema_version=schema_version)
        for item in raw_documents
    )
    _require_unique((item.document_id for item in documents), "Document IDs")
    passages = tuple(
        passage for document in documents for passage in document.passages
    )
    _require_unique((item.passage_id for item in passages), "passage IDs")
    _require_unique((item.locator_id for item in passages), "locator IDs")
    _require_unique((item.sha256 for item in documents), "Document contents")

    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("The executable evaluation corpus requires cases")
    cases = tuple(_load_case(item, schema_version=schema_version) for item in raw_cases)
    _require_unique((item.case_id for item in cases), "case IDs")

    passage_ids = {item.passage_id for item in passages}
    passage_documents = {
        passage.passage_id: document
        for document in documents
        for passage in document.passages
    }
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
        _validate_case_filters(case, passage_documents)

    return ExecutableEvaluationDataset(
        schema_version=schema_version,
        version=version,
        description=description,
        license_id=license_id,
        provenance=provenance,
        contains_sensitive_data=contains_sensitive_data,
        review_status=review_status,
        headline_eligible=headline_eligible,
        manifest_path=manifest_path,
        documents=documents,
        cases=cases,
    )


def _load_document(
    root: Path,
    raw: object,
    *,
    schema_version: int,
) -> EvaluationCorpusDocument:
    if not isinstance(raw, dict):
        raise ValueError("Evaluation corpus documents must be objects")
    document_id = _stable_id(raw, "document_id", "evaluation corpus Document")
    passages = _load_passages(raw, document_id, schema_version=schema_version)
    relative_text = _required_string(raw, "path", f"Document '{document_id}'")
    source_path = _corpus_file(root, relative_text, owner=f"Document '{document_id}'")
    relative = Path(relative_text)
    suffix = source_path.suffix.casefold()
    media_type = validate_file_format(source_path, suffix)
    content = source_path.read_bytes()
    tags = _string_tuple(raw.get("tags", []), f"Document '{document_id}' tags")
    source_uri_value = raw.get("source_uri")
    if source_uri_value is not None and (
        not isinstance(source_uri_value, str) or not source_uri_value.strip()
    ):
        raise ValueError(f"Document '{document_id}' source_uri must be a non-empty string")
    return EvaluationCorpusDocument(
        document_id=document_id,
        passages=passages,
        path=source_path,
        relative_path=relative.as_posix(),
        original_name=str(raw.get("original_name") or source_path.name),
        media_type=media_type,
        source_kind=source_kind_for_suffix(suffix),
        language=language_for_suffix(suffix),
        tags=tags,
        source_uri=source_uri_value.strip() if isinstance(source_uri_value, str) else None,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )


def _load_case(raw: object, *, schema_version: int) -> ExecutableEvaluationCase:
    if not isinstance(raw, dict):
        raise ValueError("Executable evaluation cases must be objects")
    case_id = _stable_id(raw, "case_id", "evaluation case")
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
    filters = _load_filters(raw.get("filters", {}), case_id)
    history = _load_history(raw.get("history", []), case_id)
    if schema_version == 2 and not isinstance(raw.get("reviewed"), bool):
        raise ValueError(f"Gold case '{case_id}' requires reviewed")
    if schema_version == 2 and raw.get("reviewed") is not True:
        raise ValueError(f"Gold case '{case_id}' must be reviewed")
    split = "train"
    relevance_grades: dict[str, int] = {}
    if schema_version == 2:
        split = raw.get("split")
        if split not in {"train", "dev", "test"}:
            raise ValueError(f"Gold case '{case_id}' requires a train/dev/test split")
        grades = raw.get("relevance_grades")
        if not isinstance(grades, dict):
            raise ValueError(f"Gold case '{case_id}' requires relevance_grades")
        for passage_id, grade in grades.items():
            if (
                not isinstance(passage_id, str)
                or not passage_id.strip()
                or not isinstance(grade, int)
                or isinstance(grade, bool)
                or grade < 0
            ):
                raise ValueError(f"Case '{case_id}' has an invalid relevance grade")
        relevance_grades = {passage_id: grade for passage_id, grade in grades.items()}
        if set(relevance_grades) != set(relevant):
            raise ValueError(
                f"Case '{case_id}' relevance_grades must cover exactly the relevant "
                "passages"
            )
        for passage_id in required:
            if relevance_grades.get(passage_id) != 3:
                raise ValueError(
                    f"Case '{case_id}' required passage '{passage_id}' must be graded 3"
                )
        for passage_id in set(relevant) - set(required):
            if relevance_grades.get(passage_id) != 1:
                raise ValueError(
                    f"Case '{case_id}' relevant passage '{passage_id}' must be graded 1"
                )
    return ExecutableEvaluationCase(
        case_id=case_id,
        category=category,
        query=query,
        relevant_passage_ids=relevant,
        required_passage_ids=required,
        expected_citation_pages=tuple(pages),
        expected_answer_terms=answer_terms,
        expects_supported_answer=expects_supported,
        filters=filters,
        history=history,
        split=split,
        relevance_grades=relevance_grades,
    )


def _load_passages(
    raw: dict[str, object],
    document_id: str,
    *,
    schema_version: int,
) -> tuple[EvaluationCorpusPassage, ...]:
    owner = f"Document '{document_id}'"
    if schema_version == 1:
        return (
            EvaluationCorpusPassage(
                passage_id=_stable_id(raw, "passage_id", owner),
                locator_id=_stable_id(raw, "locator_id", owner),
                locator_match={},
            ),
        )
    raw_passages = raw.get("passages")
    if not isinstance(raw_passages, list) or not raw_passages:
        raise ValueError(f"{owner} requires passages")
    passages: list[EvaluationCorpusPassage] = []
    for item in raw_passages:
        if not isinstance(item, dict):
            raise ValueError(f"{owner} passages must be objects")
        locator_match = item.get("locator_match")
        if not isinstance(locator_match, dict) or not locator_match:
            raise ValueError(f"{owner} passage locator_match must be a non-empty object")
        _validate_locator_match(locator_match, owner)
        passages.append(
            EvaluationCorpusPassage(
                passage_id=_stable_id(item, "passage_id", owner),
                locator_id=_stable_id(item, "locator_id", owner),
                locator_match=dict(locator_match),
            )
        )
    return tuple(passages)


def _validate_locator_match(locator_match: dict[str, object], owner: str) -> None:
    allowed = {"page", "section", "heading_path", "element", "symbol", "path"}
    unknown = set(locator_match) - allowed
    if unknown:
        raise ValueError(f"{owner} locator_match has unsupported keys: {sorted(unknown)}")
    for value in locator_match.values():
        if isinstance(value, bool) or not isinstance(value, (str, int, list)):
            raise ValueError(f"{owner} locator_match values must be strings, integers, or lists")
        if isinstance(value, list) and (
            not value or any(not isinstance(item, str) or not item for item in value)
        ):
            raise ValueError(f"{owner} locator_match lists must contain strings")


def _load_filters(value: object, case_id: str) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        raise ValueError(f"Case '{case_id}' filters must be an object")
    allowed = {"document_ids", "tags", "source_kinds", "languages"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"Case '{case_id}' has unsupported filters: {sorted(unknown)}")
    return {
        key: _string_tuple(items, f"Case '{case_id}' filter {key}")
        for key, items in value.items()
    }


def _load_history(value: object, case_id: str) -> tuple[EvaluationHistoryMessage, ...]:
    if not isinstance(value, list) or len(value) > 12:
        raise ValueError(f"Case '{case_id}' history must be a list of at most 12 messages")
    messages: list[EvaluationHistoryMessage] = []
    for item in value:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            raise ValueError(f"Case '{case_id}' history has an invalid role")
        content = item.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > 4_000:
            raise ValueError(f"Case '{case_id}' history has invalid content")
        messages.append(
            EvaluationHistoryMessage(role=str(item["role"]), content=content.strip())
        )
    return tuple(messages)


def _validate_case_filters(
    case: ExecutableEvaluationCase,
    passage_documents: dict[str, EvaluationCorpusDocument],
) -> None:
    for passage_id in case.relevant_passage_ids:
        document = passage_documents[passage_id]
        if (
            "document_ids" in case.filters
            and document.document_id not in case.filters["document_ids"]
        ):
            raise ValueError(f"Case '{case.case_id}' qrels violate its Document filter")
        if "tags" in case.filters and not set(case.filters["tags"]) <= set(document.tags):
            raise ValueError(f"Case '{case.case_id}' qrels violate its tag filter")
        if (
            "source_kinds" in case.filters
            and document.source_kind not in case.filters["source_kinds"]
        ):
            raise ValueError(f"Case '{case.case_id}' qrels violate its source-kind filter")
        if "languages" in case.filters and document.language not in case.filters["languages"]:
            raise ValueError(f"Case '{case.case_id}' qrels violate its language filter")


def passage_matches_locator(
    passage: EvaluationCorpusPassage,
    locator: dict[str, object],
) -> bool:
    for key, expected in passage.locator_match.items():
        actual = locator.get(key)
        if key == "page" and actual is None:
            start_page = locator.get("start_page")
            end_page = locator.get("end_page")
            actual = start_page if start_page == end_page else None
        if actual != expected:
            return False
    return True


def _required_string(payload: dict[str, object], key: str, owner: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"The {owner} requires {key}")
    return value.strip()


def _stable_id(payload: dict[str, object], key: str, owner: str) -> str:
    value = _required_string(payload, key, owner)
    if not _STABLE_ID.fullmatch(value):
        raise ValueError(
            f"The {owner} {key} must be a stable lowercase identifier"
        )
    return value


def _corpus_file(root: Path, relative_text: str, *, owner: str) -> Path:
    relative = Path(relative_text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{owner} path must stay inside the corpus")
    source_path = (root / relative).resolve()
    if not source_path.is_relative_to(root) or not source_path.is_file():
        raise ValueError(f"{owner} source file is missing")
    return source_path


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

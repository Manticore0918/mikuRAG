import math
import re
from collections.abc import Mapping
from typing import Any, Protocol

MAX_METADATA_KEYS = 64
MAX_METADATA_DEPTH = 3
MAX_METADATA_LIST_ITEMS = 64
MAX_METADATA_STRING_LENGTH = 2_000
MAX_TAGS = 32

_METADATA_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SENSITIVE_KEY_PARTS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "session",
    "token",
}
_RETRIEVAL_METADATA_KEYS = {
    "author",
    "branch",
    "commit",
    "repository",
    "revision",
    "title",
    "version",
}


class SourceDocument(Protocol):
    source_kind: str
    source_path: str | None
    source_uri: str | None
    language: str | None
    tags: list[str]
    source_metadata: dict[str, Any]


def validate_tags(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("tags must be a list")
    if len(value) > MAX_TAGS:
        raise ValueError(f"tags cannot contain more than {MAX_TAGS} items")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError("tags must contain only strings")
        cleaned = item.strip()
        if not cleaned or len(cleaned) > 64:
            raise ValueError("each tag must contain between 1 and 64 characters")
        normalized = cleaned.casefold()
        if normalized not in seen:
            seen.add(normalized)
            result.append(cleaned)
    return result


def validate_source_metadata(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("metadata must be an object")
    if len(value) > MAX_METADATA_KEYS:
        raise ValueError(f"metadata cannot contain more than {MAX_METADATA_KEYS} keys")
    return {
        _validate_key(key): _validate_value(item, depth=1)
        for key, item in value.items()
    }


def chunk_provenance(document: SourceDocument) -> dict[str, object]:
    """Return the non-secret provenance fields that are safe and useful in retrieval."""

    result: dict[str, object] = {"source_kind": document.source_kind}
    if document.source_path:
        result["source_path"] = document.source_path
    if document.source_uri:
        result["source_uri"] = document.source_uri
    if document.language:
        result["language"] = document.language
    if document.tags:
        result["tags"] = list(document.tags)
    source_metadata = document.source_metadata or {}
    for key in _RETRIEVAL_METADATA_KEYS:
        value = source_metadata.get(key)
        if isinstance(value, (str, int, bool)) or (
            isinstance(value, list) and all(isinstance(item, str) for item in value)
        ):
            result[f"source_{key}"] = value
    return result


def merge_locator(
    locator: Mapping[str, object], provenance: Mapping[str, object] | None
) -> dict[str, object]:
    result = dict(provenance or {})
    result.update(locator)
    return result


def _validate_key(key: object) -> str:
    if not isinstance(key, str) or not _METADATA_KEY.fullmatch(key):
        raise ValueError(
            "metadata keys must start with a letter and contain only letters, numbers, _, ., or -"
        )
    normalized = key.casefold().replace("-", "_").replace(".", "_")
    if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
        raise ValueError(f"metadata key '{key}' is not allowed because it may contain a secret")
    return key


def _validate_value(value: object, *, depth: int) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > MAX_METADATA_STRING_LENGTH:
            raise ValueError(
                f"metadata strings cannot exceed {MAX_METADATA_STRING_LENGTH} characters"
            )
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metadata numbers must be finite")
        return value
    if depth >= MAX_METADATA_DEPTH:
        raise ValueError(f"metadata cannot be nested more than {MAX_METADATA_DEPTH} levels")
    if isinstance(value, list):
        if len(value) > MAX_METADATA_LIST_ITEMS:
            raise ValueError(
                f"metadata lists cannot contain more than {MAX_METADATA_LIST_ITEMS} items"
            )
        return [_validate_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > MAX_METADATA_KEYS:
            raise ValueError(f"metadata objects cannot contain more than {MAX_METADATA_KEYS} keys")
        return {
            _validate_key(key): _validate_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    raise ValueError("metadata values must be JSON-compatible scalars, lists, or objects")


__all__ = [
    "chunk_provenance",
    "merge_locator",
    "validate_source_metadata",
    "validate_tags",
]

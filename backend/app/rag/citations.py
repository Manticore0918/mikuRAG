from collections.abc import Mapping

LocatorValue = int | str | list[str]

_PAGE_KEYS = {"page", "start_page", "end_page"}
_INTERNAL_KEYS = {
    "source_document_id",
    "source_content_hash",
    "summary_model",
    "summary_prompt_version",
}


def public_locator(
    locator: Mapping[str, object],
    *,
    start_page: int | None = None,
    end_page: int | None = None,
    heading_path: list[str] | None = None,
) -> dict[str, LocatorValue]:
    """Return stable citation metadata without internal hierarchy identifiers."""

    result: dict[str, LocatorValue] = {}
    for key, value in locator.items():
        if (
            key in _INTERNAL_KEYS
            or key == "parent_chunk_id"
            or key.endswith("_parent_id")
        ):
            continue
        if key in _PAGE_KEYS or key == "heading_path":
            continue
        if type(value) is int or isinstance(value, str):
            result[key] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            result[key] = list(value)

    locator_page = _positive_int(locator.get("page"))
    locator_start = _positive_int(locator.get("start_page"))
    locator_end = _positive_int(locator.get("end_page"))
    effective_start = start_page if _positive_int(start_page) is not None else locator_start
    effective_end = end_page if _positive_int(end_page) is not None else locator_end
    if effective_start is None and effective_end is None:
        effective_start = effective_end = locator_page
    elif effective_start is None:
        effective_start = effective_end
    elif effective_end is None:
        effective_end = effective_start

    if (
        effective_start is not None
        and effective_end is not None
        and effective_end >= effective_start
    ):
        result["start_page"] = effective_start
        result["end_page"] = effective_end
        if effective_start == effective_end:
            result["page"] = effective_start
    elif locator_page is not None:
        result["start_page"] = locator_page
        result["end_page"] = locator_page
        result["page"] = locator_page

    effective_heading = heading_path or locator.get("heading_path")
    if isinstance(effective_heading, list) and all(
        isinstance(item, str) for item in effective_heading
    ):
        result["heading_path"] = list(effective_heading)
    return result


def _positive_int(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None

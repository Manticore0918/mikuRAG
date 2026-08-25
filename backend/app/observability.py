import json
import logging
import math
from collections.abc import Sequence

OBSERVATION_PREFIX = "mikurag_observation "

ObservationScalar = str | int | float | bool | None
ObservationValue = ObservationScalar | list["ObservationValue"] | dict[str, "ObservationValue"]


def emit_observation(
    logger: logging.Logger,
    event: str,
    **fields: ObservationValue,
) -> None:
    payload: dict[str, ObservationValue] = {"event": event, **fields}
    logger.info(
        "%s%s",
        OBSERVATION_PREFIX,
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
    )


def token_distribution(token_counts: Sequence[int]) -> dict[str, ObservationValue]:
    if not token_counts:
        return {"count": 0}
    ordered = sorted(token_counts)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "max": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 2),
    }


def warning_page_count(
    warnings: Sequence[object],
    code: str,
) -> int:
    pages: set[int] = set()
    without_page = 0
    for warning in warnings:
        if getattr(warning, "code", None) != code:
            continue
        page_number = getattr(warning, "page_number", None)
        if type(page_number) is int and page_number > 0:
            pages.add(page_number)
        else:
            without_page += 1
    return len(pages) + without_page


def rounded_percentage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator * 100 / denominator, 2)


def _percentile(ordered: Sequence[int], fraction: float) -> int:
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]

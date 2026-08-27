from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas import RetrievalFiltersCreate, TurnCreate


def test_turn_filters_are_normalized_into_the_domain_object() -> None:
    payload = TurnCreate.model_validate(
        {
            "question": "What changed?",
            "filters": {
                "tags": [" Policy "],
                "source_kinds": ["markdown"],
                "languages": ["EN"],
                "ingested_after": "2026-08-01T00:00:00Z",
            },
        }
    )

    filters = payload.filters.to_domain()

    assert filters.tags == ("Policy",)
    assert filters.source_kinds == ("markdown",)
    assert filters.languages == ("en",)
    assert filters.ingested_after == datetime(2026, 8, 1, tzinfo=UTC)


def test_turn_filters_reject_an_inverted_ingestion_range() -> None:
    with pytest.raises(ValidationError, match="ingested_after"):
        RetrievalFiltersCreate.model_validate(
            {
                "ingested_after": "2026-08-10T00:00:00Z",
                "ingested_before": "2026-08-01T00:00:00Z",
            }
        )


def test_turn_filters_reject_timezone_naive_ingestion_dates() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        RetrievalFiltersCreate.model_validate(
            {"ingested_after": "2026-08-01T00:00:00"}
        )


def test_turn_filters_reject_unknown_source_kinds() -> None:
    with pytest.raises(ValidationError):
        RetrievalFiltersCreate.model_validate({"source_kinds": ["spreadsheet"]})

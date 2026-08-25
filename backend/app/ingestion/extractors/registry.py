from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.ingestion.contracts import ExtractedDocument
from app.ingestion.errors import ExtractionError


@dataclass(frozen=True)
class ExtractionContext:
    path: Path
    media_type: str
    max_pages: int
    source_kind: str
    source_path: str | None = None
    source_uri: str | None = None
    language: str | None = None


class Extractor(Protocol):
    media_types: frozenset[str]
    parser_version: str

    def extract(self, context: ExtractionContext) -> ExtractedDocument: ...


@dataclass(frozen=True)
class FunctionExtractor:
    media_types: frozenset[str]
    parser_version: str
    function: Callable[[ExtractionContext], ExtractedDocument]

    def extract(self, context: ExtractionContext) -> ExtractedDocument:
        return self.function(context)


class ExtractorRegistry:
    def __init__(self, extractors: Iterable[Extractor] = ()) -> None:
        self._extractors: dict[str, Extractor] = {}
        for extractor in extractors:
            self.register(extractor)

    def register(self, extractor: Extractor) -> None:
        for media_type in extractor.media_types:
            if media_type in self._extractors:
                raise ValueError(f"An extractor is already registered for {media_type}")
            self._extractors[media_type] = extractor

    def resolve(self, media_type: str) -> Extractor:
        try:
            return self._extractors[media_type]
        except KeyError as error:
            raise ExtractionError("This Document format has no registered extractor") from error

    @property
    def media_types(self) -> frozenset[str]:
        return frozenset(self._extractors)


__all__ = [
    "ExtractionContext",
    "Extractor",
    "ExtractorRegistry",
    "FunctionExtractor",
]

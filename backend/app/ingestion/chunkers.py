"""Versioned chunker profiles behind a single Chunker boundary.

Checkpoint 2 introduces a Chunker interface so experiments are identified by a
versioned profile plus a canonical configuration hash, never by a single mutable
global setting. Three profiles ship:

- ``legacy_char_v1``: the original character-target/overlap splitter.
- ``token_recursive_v1``: a token-aware recursive splitter with the same
  section-preserving locators as the character splitter.
- ``hierarchical_v1``: parent/child token hierarchy with embedded heading paths.

``legacy`` remains accepted as an alias of ``legacy_char_v1`` so existing
Installations and tests keep working; the alias stores its own profile string.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings
from app.ingestion.chunking import TextChunk, chunk_sections
from app.ingestion.contracts import ExtractedDocument, ExtractedSection
from app.ingestion.hierarchical_chunking import (
    ConstructedHierarchy,
    HierarchicalChunkingConfig,
    construct_hierarchy,
)
from app.ingestion.tokenization import Tokenizer, create_tokenizer

_CHUNKER_PROFILES = ("legacy_char_v1", "token_recursive_v1", "hierarchical_v1")

_ALIASES = {"legacy": "legacy_char_v1"}

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ChunkerConfig:
    """Versioned chunker identity: profile plus every tuning parameter."""

    profile: str
    parameters: dict[str, object]
    config_hash: str = ""

    def __post_init__(self) -> None:
        payload = json.dumps(
            {"profile": self.profile, "parameters": self.parameters},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        object.__setattr__(
            self,
            "config_hash",
            hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )


class Chunker(Protocol):
    profile: str
    config: ChunkerConfig

    def chunk(
        self, document: ExtractedDocument
    ) -> list[TextChunk] | ConstructedHierarchy: ...


class LegacyCharacterChunker:
    """Character-target splitter; the compatibility baseline."""

    profile = "legacy_char_v1"

    def __init__(
        self,
        *,
        profile: str,
        settings: Settings,
        tokenizer: Tokenizer,
    ) -> None:
        self.profile = profile
        self.tokenizer = tokenizer
        self.settings = settings
        self.config = ChunkerConfig(
            profile=profile,
            parameters={
                "target_characters": settings.chunk_target_characters,
                "overlap_characters": settings.chunk_overlap_characters,
            },
        )

    def chunk(self, document: ExtractedDocument) -> list[TextChunk]:
        return chunk_sections(
            document.sections,
            self.settings.chunk_target_characters,
            self.settings.chunk_overlap_characters,
        )


class TokenRecursiveChunker:
    """Token-aware recursive splitter preserving section-level locators."""

    profile = "token_recursive_v1"

    def __init__(
        self,
        *,
        profile: str,
        settings: Settings,
        tokenizer: Tokenizer,
    ) -> None:
        self.profile = profile
        self.tokenizer = tokenizer
        self.config = ChunkerConfig(
            profile=profile,
            parameters={
                "tokenizer": settings.chunk_tokenizer,
                "min_tokens": settings.child_min_tokens,
                "target_tokens": settings.child_target_tokens,
                "max_tokens": settings.child_max_tokens,
                "overlap_tokens": settings.child_overlap_tokens,
            },
        )

    def chunk(self, document: ExtractedDocument) -> list[TextChunk]:
        parameters = self.config.parameters
        return _token_recursive_chunks(
            document.sections,
            tokenizer=self.tokenizer,
            min_tokens=parameters["min_tokens"],
            target_tokens=parameters["target_tokens"],
            max_tokens=parameters["max_tokens"],
            overlap_tokens=parameters["overlap_tokens"],
        )


class HierarchicalChunker:
    """Token-aware parent/child hierarchy with embedded heading paths."""

    profile = "hierarchical_v1"

    def __init__(
        self,
        *,
        profile: str,
        settings: Settings,
        tokenizer: Tokenizer,
    ) -> None:
        self.profile = profile
        self.tokenizer = tokenizer
        self.config = ChunkerConfig(
            profile=profile,
            parameters={
                "tokenizer": settings.chunk_tokenizer,
                "child_min_tokens": settings.child_min_tokens,
                "child_target_tokens": settings.child_target_tokens,
                "child_max_tokens": settings.child_max_tokens,
                "child_overlap_tokens": settings.child_overlap_tokens,
                "parent_target_tokens": settings.parent_target_tokens,
                "parent_max_tokens": settings.parent_max_tokens,
            },
        )
        self.hierarchical_config = HierarchicalChunkingConfig(
            child_min_tokens=settings.child_min_tokens,
            child_target_tokens=settings.child_target_tokens,
            child_max_tokens=settings.child_max_tokens,
            child_overlap_tokens=settings.child_overlap_tokens,
            parent_target_tokens=settings.parent_target_tokens,
            parent_max_tokens=settings.parent_max_tokens,
            chunking_version=self.profile,
        )

    def chunk(self, document: ExtractedDocument) -> ConstructedHierarchy:
        return construct_hierarchy(
            document,
            config=self.hierarchical_config,
            tokenizer=self.tokenizer,
        )


def build_chunker(
    settings: Settings,
    *,
    version: str | None = None,
    tokenizer: Tokenizer | None = None,
) -> Chunker:
    """Build the chunker named by ``version`` or ``settings.chunking_version``."""
    profile = version or settings.chunking_version
    canonical = _ALIASES.get(profile, profile)
    if canonical not in _CHUNKER_PROFILES:
        raise ValueError(f"Unsupported chunking profile: {profile}")
    active_tokenizer = tokenizer or create_tokenizer(settings.chunk_tokenizer)
    if canonical == "legacy_char_v1":
        return LegacyCharacterChunker(
            profile=profile, settings=settings, tokenizer=active_tokenizer
        )
    if canonical == "token_recursive_v1":
        return TokenRecursiveChunker(
            profile=profile, settings=settings, tokenizer=active_tokenizer
        )
    return HierarchicalChunker(
        profile=profile, settings=settings, tokenizer=active_tokenizer
    )


def canonical_profile(profile: str) -> str:
    return _ALIASES.get(profile, profile)


def is_hierarchical_chunker(chunker: Chunker) -> bool:
    return isinstance(chunker, HierarchicalChunker)


# ---------------------------------------------------------------- token split


def _token_recursive_chunks(
    sections: list[ExtractedSection],
    *,
    tokenizer: Tokenizer,
    min_tokens: int,
    target_tokens: int,
    max_tokens: int,
    overlap_tokens: int,
) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for section in sections:
        units = _split_units(section.text)
        part = 1
        packed = _pack_units(
            units,
            tokenizer=tokenizer,
            min_tokens=min_tokens,
            target_tokens=target_tokens,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
        )
        for piece in packed:
            locator = dict(section.locator)
            locator["part"] = part
            chunks.append(TextChunk(text=piece, locator=locator))
            part += 1
    return chunks


def _split_units(text: str) -> list[str]:
    units: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        sentences = [
            sentence.strip()
            for sentence in _SENTENCE.split(paragraph)
            if sentence.strip()
        ]
        if len(sentences) > 1:
            units.extend(sentences)
        else:
            units.append(paragraph)
    return units


def _pack_units(
    units: list[str],
    *,
    tokenizer: Tokenizer,
    min_tokens: int,
    target_tokens: int,
    max_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0
    unprocessed_tokens = sum(tokenizer.count(unit) for unit in units)

    def flush() -> None:
        nonlocal current, current_tokens
        if current:
            pieces.append(" ".join(current))
        current = []
        current_tokens = 0

    for unit in units:
        unit_tokens = tokenizer.count(unit)
        unprocessed_tokens -= unit_tokens
        if unit_tokens > max_tokens:
            flush()
            for part in tokenizer.split(unit, max_tokens):
                pieces.append(part)
            continue
        if current and current_tokens >= target_tokens:
            remaining = unit_tokens + unprocessed_tokens
            can_absorb = (
                remaining < min_tokens
                and current_tokens + unit_tokens <= max_tokens
            )
            if not can_absorb:
                flush()
        if current_tokens + unit_tokens > max_tokens:
            if current_tokens >= min_tokens:
                flush()
            else:
                tail = current.pop() if current else ""
                current_tokens = tokenizer.count(tail) if tail else 0
        current.append(unit)
        current_tokens += unit_tokens

    flush()

    if overlap_tokens > 0 and len(pieces) >= 2:
        overlapped: list[str] = [pieces[0]]
        previous = pieces[0]
        for piece in pieces[1:]:
            prefix, _ = _tail_units([previous], overlap_tokens, tokenizer=tokenizer)
            merged = " ".join([*prefix, piece])
            if tokenizer.count(merged) > max_tokens:
                merged = piece
            overlapped.append(merged)
            previous = piece
        pieces = overlapped
    return pieces


def _tail_units(
    units: list[str],
    max_tokens: int,
    *,
    tokenizer: Tokenizer,
) -> tuple[list[str], int]:
    selected: list[str] = []
    remaining = max_tokens
    for unit in reversed(units):
        unit_tokens = tokenizer.count(unit)
        if unit_tokens <= remaining:
            selected.append(unit)
            remaining -= unit_tokens
        else:
            tail = tokenizer.tail(unit, remaining)
            if tail:
                selected.append(tail)
            remaining = 0
        if remaining <= 0:
            break
    selected.reverse()
    return selected, max_tokens - remaining

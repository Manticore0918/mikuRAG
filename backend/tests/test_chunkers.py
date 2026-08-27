"""Tests for the Chunker interface and its three versioned profiles."""

import pytest

from app.config import Settings
from app.ingestion.chunkers import (
    ChunkerConfig,
    HierarchicalChunker,
    LegacyCharacterChunker,
    TokenRecursiveChunker,
    build_chunker,
    canonical_profile,
    is_hierarchical_chunker,
)
from app.ingestion.contracts import ExtractedBlock, ExtractedDocument
from app.ingestion.hierarchical_chunking import ConstructedHierarchy
from app.ingestion.tokenization import ConservativeTokenizer


def _settings(**overrides) -> Settings:
    values = {
        "session_secret": "s" * 32,
        "encryption_master_key": "e" * 32,
    }
    values.update(overrides)
    return Settings(**values)


def _document(texts: list[str] | None = None) -> ExtractedDocument:
    sources = texts or [
        "The first section explains the reimbursement policy in detail.",
        "The second section lists the equipment allowance ceiling.",
    ]
    return ExtractedDocument(
        blocks=[
            ExtractedBlock(
                text=text,
                block_type="paragraph",
                order=index,
                heading_path=["Acme Policy", f"Section {index + 1}"],
                metadata={"locator": {"section": f"section-{index + 1}", "part": 1}},
            )
            for index, text in enumerate(sources)
        ],
        page_count=1,
    )


def test_config_hash_is_stable_and_distinct() -> None:
    first = ChunkerConfig(
        profile="token_recursive_v1",
        parameters={"target_tokens": 500, "overlap_tokens": 60},
    )
    second = ChunkerConfig(
        profile="token_recursive_v1",
        parameters={"target_tokens": 500, "overlap_tokens": 60},
    )
    different = ChunkerConfig(
        profile="token_recursive_v1",
        parameters={"target_tokens": 750, "overlap_tokens": 60},
    )

    assert len(first.config_hash) == 64
    assert first.config_hash == second.config_hash
    assert first.config_hash != different.config_hash


@pytest.mark.parametrize(
    ("profile", "chunker_type", "hierarchical"),
    [
        ("legacy", LegacyCharacterChunker, False),
        ("legacy_char_v1", LegacyCharacterChunker, False),
        ("token_recursive_v1", TokenRecursiveChunker, False),
        ("hierarchical_v1", HierarchicalChunker, True),
    ],
)
def test_build_chunker_supports_every_profile(
    profile: str, chunker_type, hierarchical: bool
) -> None:
    chunker = build_chunker(_settings(chunking_version=profile))
    assert isinstance(chunker, chunker_type)
    assert chunker.profile == profile
    assert len(chunker.config.config_hash) == 64
    assert is_hierarchical_chunker(chunker) is hierarchical


def test_version_override_and_alias() -> None:
    settings = _settings(chunking_version="legacy")
    assert canonical_profile("legacy") == "legacy_char_v1"
    assert canonical_profile("token_recursive_v1") == "token_recursive_v1"
    assert build_chunker(settings, version="hierarchical_v1").profile == "hierarchical_v1"


def test_build_chunker_rejects_unknown_profile() -> None:
    with pytest.raises(ValueError):
        build_chunker(_settings(chunking_version="not_a_profile"))


def test_chunkers_are_deterministic() -> None:
    document = _document()
    for profile in ("legacy_char_v1", "token_recursive_v1", "hierarchical_v1"):
        chunker = build_chunker(_settings(chunking_version=profile))
        first = chunker.chunk(document)
        second = chunker.chunk(document)
        if isinstance(first, ConstructedHierarchy):
            assert isinstance(second, ConstructedHierarchy)
            assert [c.text for c in first.children] == [c.text for c in second.children]
            assert [c.ordinal for c in first.children] == [c.ordinal for c in second.children]
        else:
            assert [chunk.text for chunk in first] == [chunk.text for chunk in second]


def test_legacy_chunker_splits_by_character_target() -> None:
    document = _document()
    chunks = build_chunker(_settings(chunking_version="legacy_char_v1")).chunk(document)
    assert len(chunks) == 2
    assert all(chunk.text for chunk in chunks)
    assert "reimbursement" in chunks[0].text
    assert "equipment" in chunks[1].text


def test_token_recursive_preserves_section_locators_with_part() -> None:
    document = _document()
    chunks = build_chunker(
        _settings(chunking_version="token_recursive_v1")
    ).chunk(document)
    assert len(chunks) == 2
    assert all(chunk.locator.get("section") for chunk in chunks)
    assert all(chunk.locator.get("part") == 1 for chunk in chunks)
    assert chunks[0].locator["heading_path"] == ["Acme Policy", "Section 1"]


def test_token_recursive_respects_max_tokens() -> None:
    long_text = " ".join(
        [
            "The policy requires employees to keep all travel receipts for inspection "
            "and to submit an expense report within thirty calendar days."
        ]
        * 60
    )
    document = _document([long_text])
    settings = _settings(
        chunking_version="token_recursive_v1",
        child_min_tokens=100,
        child_target_tokens=300,
        child_max_tokens=400,
        child_overlap_tokens=40,
    )
    chunker = build_chunker(settings)
    tokenizer = ConservativeTokenizer()
    chunks = chunker.chunk(document)
    assert len(chunks) >= 3
    for chunk in chunks:
        assert tokenizer.count(chunk.text) <= 400
    # The section is a single source boundary, so every chunk carries part markers.
    assert sorted({chunk.locator["part"] for chunk in chunks}) == sorted(
        range(1, len(chunks) + 1)
    )


def test_hierarchical_chunker_returns_constructed_hierarchy() -> None:
    document = _document()
    chunker = build_chunker(_settings(chunking_version="hierarchical_v1"))
    hierarchy = chunker.chunk(document)
    assert isinstance(hierarchy, ConstructedHierarchy)
    assert hierarchy.parents
    assert hierarchy.children
    assert chunker.hierarchical_config.chunking_version == "hierarchical_v1"
    assert all(child.chunking_version == "hierarchical_v1" for child in hierarchy.children)

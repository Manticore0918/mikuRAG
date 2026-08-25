from app.ingestion.contracts import ExtractedBlock, ExtractedDocument
from app.ingestion.hierarchical_chunking import (
    HierarchicalChunkingConfig,
    construct_hierarchy,
)
from app.ingestion.tokenization import ConservativeTokenizer


def config(
    *,
    child_min: int = 4,
    child_target: int = 6,
    child_max: int = 9,
    overlap: int = 2,
    parent_target: int = 18,
    parent_max: int = 30,
) -> HierarchicalChunkingConfig:
    return HierarchicalChunkingConfig(
        child_min_tokens=child_min,
        child_target_tokens=child_target,
        child_max_tokens=child_max,
        child_overlap_tokens=overlap,
        parent_target_tokens=parent_target,
        parent_max_tokens=parent_max,
        chunking_version="test_v1",
    )


def block(
    text: str,
    order: int,
    *,
    block_type: str = "paragraph",
    heading_path: list[str] | None = None,
    page: int | None = None,
    start_offset: int = 0,
) -> ExtractedBlock:
    return ExtractedBlock(
        text=text,
        block_type=block_type,
        order=order,
        start_page=page,
        end_page=page,
        heading_level=1 if block_type == "heading" else None,
        heading_path=heading_path or [],
        metadata={
            "start_offset": start_offset,
            "end_offset": start_offset + len(text),
        },
    )


def test_conservative_tokenizer_is_bounded_and_multilingual() -> None:
    tokenizer = ConservativeTokenizer()

    assert tokenizer.count("abcdefgh 中国!") == 5
    assert tokenizer.split("abcdefgh 中国!", 2) == ["abcdefgh", "中国", "!"]
    assert tokenizer.tail("one two three", 2) == "three"


def test_heading_boundaries_create_distinct_parents_and_parent_links() -> None:
    document = ExtractedDocument(
        blocks=[
            block("Alpha", 0, block_type="heading", heading_path=["Alpha"]),
            block("a1 a2 a3 a4", 1, heading_path=["Alpha"], start_offset=7),
            block("Beta", 2, block_type="heading", heading_path=["Beta"], start_offset=20),
            block("b1 b2 b3 b4", 3, heading_path=["Beta"], start_offset=26),
        ],
        page_count=None,
    )

    hierarchy = construct_hierarchy(document, config=config(parent_target=25, parent_max=35))

    assert len(hierarchy.parents) == 2
    assert [parent.heading_path for parent in hierarchy.parents] == [["Alpha"], ["Beta"]]
    assert {child.parent_ordinal for child in hierarchy.children} == {0, 1}
    assert all(
        ("Alpha" in child.text) == (child.parent_ordinal == 0)
        for child in hierarchy.children
        if child.text.startswith(("Alpha", "Beta"))
    )


def test_children_respect_maximum_and_overlap_stays_within_parent() -> None:
    text = "a1 a2 a3. a4 a5 a6. a7 a8 a9. a10 a11 a12."
    document = ExtractedDocument(
        blocks=[block(text, 0, page=4)],
        page_count=4,
    )
    active_config = config(child_min=3, child_target=5, child_max=7, overlap=2)
    tokenizer = ConservativeTokenizer()

    hierarchy = construct_hierarchy(
        document,
        config=active_config,
        tokenizer=tokenizer,
    )

    assert len(hierarchy.children) >= 2
    assert all(child.token_count <= active_config.child_max_tokens for child in hierarchy.children)
    assert all(
        tokenizer.count(child.embedding_text or "") <= active_config.child_max_tokens
        for child in hierarchy.children
    )
    assert "a5 a6" in hierarchy.children[1].text
    assert all(child.locator["page"] == 4 for child in hierarchy.children)


def test_large_tables_repeat_header_in_each_child() -> None:
    table = "\n".join(
        [
            "H | V",
            "a | 1",
            "b | 2",
            "c | 3",
            "d | 4",
        ]
    )
    document = ExtractedDocument(
        blocks=[block(table, 0, block_type="table")],
        page_count=None,
    )

    hierarchy = construct_hierarchy(
        document,
        config=config(
            child_min=3,
            child_target=6,
            child_max=8,
            overlap=2,
            parent_target=20,
            parent_max=40,
        ),
    )

    assert len(hierarchy.children) == 4
    assert all(child.text.startswith("H | V\n") for child in hierarchy.children)
    assert all(child.text.count("H | V") == 1 for child in hierarchy.children)
    assert all(child.content_type == "table" for child in hierarchy.children)


def test_small_table_stays_intact_and_sentence_boundaries_are_preferred() -> None:
    table_document = ExtractedDocument(
        blocks=[block("Key | Value\nMode | Safe", 0, block_type="table")],
        page_count=None,
    )
    sentence_document = ExtractedDocument(
        blocks=[block("One two three. Four five six. Seven eight nine.", 0)],
        page_count=None,
    )
    active_config = config(
        child_min=2,
        child_target=5,
        child_max=7,
        overlap=0,
        parent_target=16,
        parent_max=24,
    )

    table_hierarchy = construct_hierarchy(table_document, config=active_config)
    sentence_hierarchy = construct_hierarchy(sentence_document, config=active_config)

    assert [child.text for child in table_hierarchy.children] == [
        "Key | Value\nMode | Safe"
    ]
    assert [child.text for child in sentence_hierarchy.children] == [
        "One two three.",
        "Four five six.",
        "Seven eight nine.",
    ]


def test_overlap_never_crosses_unrelated_heading_sections() -> None:
    document = ExtractedDocument(
        blocks=[
            block("Alpha", 0, block_type="heading", heading_path=["Alpha"]),
            block(
                "alpha1 alpha2 alpha3. alpha4 alpha5 alpha6.",
                1,
                heading_path=["Alpha"],
            ),
            block("Beta", 2, block_type="heading", heading_path=["Beta"]),
            block(
                "beta1 beta2 beta3. beta4 beta5 beta6.",
                3,
                heading_path=["Beta"],
            ),
        ],
        page_count=None,
    )

    hierarchy = construct_hierarchy(
        document,
        config=config(
            child_min=3,
            child_target=5,
            child_max=8,
            overlap=2,
            parent_target=16,
            parent_max=24,
        ),
    )

    alpha_children = [
        child for child in hierarchy.children if child.parent_ordinal == 0
    ]
    beta_children = [
        child for child in hierarchy.children if child.parent_ordinal == 1
    ]
    assert alpha_children and beta_children
    assert all("beta" not in child.text.casefold() for child in alpha_children)
    assert all("alpha" not in child.text.casefold() for child in beta_children)


def test_cross_page_ranges_embedding_prefixes_and_hashes_are_stable() -> None:
    document = ExtractedDocument(
        blocks=[
            block(
                "Cross Page",
                0,
                block_type="heading",
                heading_path=["Guide", "Cross Page"],
                page=2,
            ),
            ExtractedBlock(
                text="a1 a2 a3 a4",
                block_type="paragraph",
                order=1,
                start_page=2,
                end_page=3,
                heading_path=["Guide", "Cross Page"],
                metadata={"start_offset": 12, "end_offset": 23},
            ),
        ],
        page_count=3,
    )
    active_config = config(child_max=12, parent_target=20, parent_max=30)

    first = construct_hierarchy(document, config=active_config)
    second = construct_hierarchy(document, config=active_config)

    assert first == second
    assert first.children[0].locator == {
        "start_page": 2,
        "end_page": 3,
        "heading_path": ["Guide", "Cross Page"],
    }
    assert first.children[0].embedding_text.startswith("Guide > Cross Page\n\n")
    assert len(first.children[0].content_hash) == 64


def test_oversized_single_sentence_uses_hard_token_split() -> None:
    text = "abcdefghijklmnopqrstuvwxyz0123456789"
    document = ExtractedDocument(blocks=[block(text, 0)], page_count=None)
    active_config = config(child_min=2, child_target=3, child_max=4, overlap=0)

    hierarchy = construct_hierarchy(document, config=active_config)

    assert len(hierarchy.children) >= 2
    assert all(child.token_count <= 4 for child in hierarchy.children)
    assert "".join(child.text for child in hierarchy.children) == text


def test_long_heading_prefix_is_bounded_for_embedding_input() -> None:
    heading = "abcdefghijklmnopqrstuvwxyz0123456789"
    document = ExtractedDocument(
        blocks=[
            block(heading, 0, block_type="heading", heading_path=[heading]),
            block("a1 a2 a3 a4", 1, heading_path=[heading]),
        ],
        page_count=None,
    )
    active_config = config(child_min=2, child_target=4, child_max=7, overlap=0)
    tokenizer = ConservativeTokenizer()

    hierarchy = construct_hierarchy(
        document,
        config=active_config,
        tokenizer=tokenizer,
    )

    assert all(
        tokenizer.count(child.embedding_text or "") <= active_config.child_max_tokens
        for child in hierarchy.children
    )


def test_generated_block_sequence_preserves_markers_and_stable_ordering() -> None:
    blocks = [
        block(
            f"marker{i} a1 a2 a3 a4.",
            i,
            page=(i // 3) + 1,
            start_offset=i * 30,
        )
        for i in range(12)
    ]
    document = ExtractedDocument(blocks=blocks, page_count=4)
    active_config = config(
        child_min=3,
        child_target=6,
        child_max=9,
        overlap=0,
        parent_target=12,
        parent_max=16,
    )
    tokenizer = ConservativeTokenizer()

    hierarchy = construct_hierarchy(
        document,
        config=active_config,
        tokenizer=tokenizer,
    )

    parent_text = "\n".join(parent.text for parent in hierarchy.parents)
    assert all(f"marker{i}" in parent_text for i in range(12))
    assert [parent.ordinal for parent in hierarchy.parents] == list(
        range(len(hierarchy.parents))
    )
    assert [child.ordinal for child in hierarchy.children] == list(
        range(len(hierarchy.children))
    )
    assert all(
        parent.token_count <= active_config.parent_max_tokens
        for parent in hierarchy.parents
    )
    assert all(child.token_count <= active_config.child_max_tokens for child in hierarchy.children)
    assert all(
        hierarchy.parents[child.parent_ordinal].start_offset <= child.start_offset
        for child in hierarchy.children
        if child.parent_ordinal is not None
        and child.start_offset is not None
        and hierarchy.parents[child.parent_ordinal].start_offset is not None
    )

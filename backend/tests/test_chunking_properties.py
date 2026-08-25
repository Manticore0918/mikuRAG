import random

from app.ingestion.contracts import ExtractedBlock, ExtractedDocument
from app.ingestion.hierarchical_chunking import (
    HierarchicalChunkingConfig,
    construct_hierarchy,
)
from app.ingestion.tokenization import ConservativeTokenizer


def _generated_document(seed: int) -> ExtractedDocument:
    randomizer = random.Random(seed)
    blocks: list[ExtractedBlock] = []
    heading_path: list[str] = []
    page = 1
    offset = 0
    block_types = ("paragraph", "paragraph", "list_item", "table", "code")

    for order in range(randomizer.randint(12, 30)):
        if order % randomizer.randint(4, 7) == 0:
            text = f"Section {seed}-{order}"
            block_type = "heading"
            heading_path = [text]
            heading_level = 1
        else:
            block_type = randomizer.choice(block_types)
            marker = f"marker{seed}x{order}"
            words = [marker]
            words.extend(
                f"w{order}_{index}"
                for index in range(randomizer.randint(2, 22))
            )
            if block_type == "table":
                rows = ["Key | Value"]
                rows.extend(
                    f"{word} | {index}" for index, word in enumerate(words)
                )
                text = "\n".join(rows)
            elif block_type == "code":
                text = " ".join(words)
            else:
                text = " ".join(words) + "."
            heading_level = None

        end_page = page + (1 if randomizer.random() < 0.12 else 0)
        blocks.append(
            ExtractedBlock(
                text=text,
                block_type=block_type,
                order=order,
                start_page=page,
                end_page=end_page,
                heading_level=heading_level,
                heading_path=list(heading_path),
                metadata={
                    "start_offset": offset,
                    "end_offset": offset + len(text),
                },
            )
        )
        offset += len(text) + 2
        page = end_page + (1 if randomizer.random() < 0.25 else 0)

    return ExtractedDocument(blocks=blocks, page_count=page)


def test_generated_block_sequences_preserve_hierarchy_invariants() -> None:
    tokenizer = ConservativeTokenizer()
    config = HierarchicalChunkingConfig(
        child_min_tokens=3,
        child_target_tokens=8,
        child_max_tokens=12,
        child_overlap_tokens=2,
        parent_target_tokens=20,
        parent_max_tokens=32,
        chunking_version="property_v1",
    )

    for seed in range(25):
        document = _generated_document(seed)
        first = construct_hierarchy(document, config=config, tokenizer=tokenizer)
        second = construct_hierarchy(document, config=config, tokenizer=tokenizer)
        source_orders = {block.order for block in document.blocks}
        parent_orders = {
            order for parent in first.parents for order in parent.source_block_orders
        }
        child_orders = {
            order for child in first.children for order in child.source_block_orders
        }

        assert first == second
        assert parent_orders == source_orders
        assert source_orders <= child_orders
        assert [chunk.ordinal for chunk in first.parents] == list(
            range(len(first.parents))
        )
        assert [chunk.ordinal for chunk in first.children] == list(
            range(len(first.children))
        )
        assert all(chunk.token_count <= config.parent_max_tokens for chunk in first.parents)
        assert all(chunk.token_count <= config.child_max_tokens for chunk in first.children)
        assert all(
            chunk.start_page is None
            or chunk.end_page is None
            or 1 <= chunk.start_page <= chunk.end_page <= document.page_count
            for chunk in [*first.parents, *first.children]
        )
        assert all(
            child.parent_ordinal is not None
            and set(child.source_block_orders)
            <= set(first.parents[child.parent_ordinal].source_block_orders)
            for child in first.children
        )

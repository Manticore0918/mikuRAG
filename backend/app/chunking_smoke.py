import json

from app.config import get_settings
from app.demo_seed import ASSETS, DATA_DIR
from app.ingestion.chunking import chunk_sections
from app.ingestion.extraction import extract_document
from app.ingestion.hierarchical_chunking import (
    HierarchicalChunkingConfig,
    construct_hierarchy,
)
from app.ingestion.normalization import normalize_document
from app.ingestion.tokenization import create_tokenizer
from app.ingestion.validation import validate_document_limits, validate_hierarchy


def run_smoke() -> dict[str, dict[str, int]]:
    settings = get_settings()
    tokenizer = create_tokenizer(settings.chunk_tokenizer)
    hierarchical_config = HierarchicalChunkingConfig(
        child_min_tokens=settings.child_min_tokens,
        child_target_tokens=settings.child_target_tokens,
        child_max_tokens=settings.child_max_tokens,
        child_overlap_tokens=settings.child_overlap_tokens,
        parent_target_tokens=settings.parent_target_tokens,
        parent_max_tokens=settings.parent_max_tokens,
        chunking_version="hierarchical_v1",
    )
    report: dict[str, dict[str, int]] = {}
    for filename, media_type in ASSETS:
        normalized = normalize_document(
            extract_document(
                DATA_DIR / filename,
                media_type,
                settings.max_document_pages,
            )
        )
        validate_document_limits(
            normalized,
            tokenizer=tokenizer,
            max_document_tokens=settings.max_document_tokens,
        )
        legacy = chunk_sections(
            normalized.sections,
            settings.chunk_target_characters,
            settings.chunk_overlap_characters,
        )
        hierarchy = construct_hierarchy(
            normalized,
            config=hierarchical_config,
            tokenizer=tokenizer,
        )
        validate_hierarchy(
            normalized,
            hierarchy,
            config=hierarchical_config,
            tokenizer=tokenizer,
            max_document_chunks=settings.max_document_chunks,
            max_document_tokens=settings.max_document_tokens,
        )
        if not legacy or not hierarchy.parents or not hierarchy.children:
            raise SystemExit(f"Chunking smoke failed for {filename}")
        report[filename] = {
            "legacy_children": len(legacy),
            "hierarchical_parents": len(hierarchy.parents),
            "hierarchical_children": len(hierarchy.children),
        }
    return report


def main() -> None:
    print(json.dumps(run_smoke(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

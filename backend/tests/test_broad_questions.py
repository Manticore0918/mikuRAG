import hashlib
import json
import uuid

import pytest

from app.config import Settings
from app.ingestion.hierarchical_chunking import ConstructedChunk
from app.ingestion.persistence import build_hierarchical_chunk_models, build_summary_chunk_models
from app.ingestion.summarization import generate_hierarchical_summaries
from app.ingestion.tokenization import ConservativeTokenizer
from app.models import ChunkContentType, ChunkLevel
from app.rag.grounding import grounded_messages
from app.rag.query_classification import DeterministicQueryClassifier, QueryKind
from app.rag.summary_retrieval import SummaryContext


def settings(**overrides) -> Settings:
    return Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        chunking_version="hierarchical_v1",
        **overrides,
    )


def parent(ordinal: int, text: str) -> ConstructedChunk:
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    return ConstructedChunk(
        text=text,
        embedding_text=None,
        chunk_level=ChunkLevel.PARENT,
        ordinal=ordinal,
        parent_ordinal=None,
        start_page=ordinal + 1,
        end_page=ordinal + 1,
        start_offset=ordinal * 100,
        end_offset=ordinal * 100 + len(text),
        heading_path=[f"Section {ordinal + 1}"],
        content_type=ChunkContentType.PARAGRAPH,
        token_count=len(text.split()),
        chunking_version="hierarchical_v1",
        content_hash=content_hash,
        locator={"page": ordinal + 1},
        source_block_orders=(ordinal,),
    )


def test_deterministic_query_classifier_routes_three_query_shapes() -> None:
    classifier = DeterministicQueryClassifier()

    assert classifier.classify("What is the leave limit?").kind == QueryKind.NARROW_FACT
    assert (
        classifier.classify("Compare the leave policy and travel policy.").kind
        == QueryKind.MULTI_PART
    )
    assert (
        classifier.classify("Give me a comprehensive summary of all documents.").kind
        == QueryKind.BROAD
    )
    assert classifier.classify("List all requirements.").kind == QueryKind.BROAD


@pytest.mark.asyncio
async def test_hierarchical_summaries_use_map_reduce_and_record_provenance() -> None:
    class Generator:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def summarize(
            self,
            *,
            purpose: str,
            source_text: str,
            heading_path: list[str],
        ) -> str:
            self.calls.append((purpose, source_text))
            return f"{purpose} result {len(self.calls)}"

    source_parents = [
        parent(0, "Alpha policy details."),
        parent(1, "Beta policy details."),
        parent(2, "Gamma policy details."),
    ]
    generator = Generator()
    summaries = await generate_hierarchical_summaries(
        source_parents,
        document_name="policies.md",
        settings=settings(summary_reduce_batch_size=2),
        tokenizer=ConservativeTokenizer(),
        generator=generator,
    )

    assert [item.chunk_level for item in summaries] == [
        ChunkLevel.SECTION_SUMMARY,
        ChunkLevel.SECTION_SUMMARY,
        ChunkLevel.SECTION_SUMMARY,
        ChunkLevel.DOCUMENT_SUMMARY,
    ]
    assert [purpose for purpose, _ in generator.calls].count("document_reduce") == 3
    assert generator.calls[-1][0] == "document_final"
    assert summaries[0].source_content_hash == source_parents[0].content_hash
    assert summaries[-1].source_parent_ordinal is None
    assert summaries[-1].start_page == 1
    assert summaries[-1].end_page == 3
    assert summaries[-1].summary_model == settings().generation_model_id


@pytest.mark.asyncio
async def test_summary_models_link_sections_and_store_source_metadata() -> None:
    source_parent = parent(0, "Source policy details.")

    class Generator:
        async def summarize(
            self,
            *,
            purpose: str,
            source_text: str,
            heading_path: list[str],
        ) -> str:
            return f"{purpose} summary"

    active_settings = settings()
    generated = await generate_hierarchical_summaries(
        [source_parent],
        document_name="policy.md",
        settings=active_settings,
        tokenizer=ConservativeTokenizer(),
        generator=Generator(),
    )
    document_id = uuid.uuid4()
    batch = build_hierarchical_chunk_models(
        document_id=document_id,
        hierarchy=type(
            "Hierarchy",
            (),
            {
                "parents": [source_parent],
                "children": [],
            },
        )(),
        vectors=[],
        embedding_model="embed-v1",
    )
    models = build_summary_chunk_models(
        document_id=document_id,
        parents=batch.parents,
        summaries=generated,
        vectors=[[0.1] * 768 for _ in generated],
        embedding_model="embed-v1",
    )

    section, document = models
    assert section.parent_chunk_id == batch.parents[0].id
    assert document.parent_chunk_id is None
    assert section.content_type == ChunkContentType.SUMMARY
    assert section.locator["source_parent_id"] == str(batch.parents[0].id)
    assert section.locator["source_content_hash"] == source_parent.content_hash
    assert document.locator["source_document_id"] == str(document_id)


def test_grounding_treats_summaries_as_non_citable_planning_context() -> None:
    summary = SummaryContext(
        summary_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_name="policy.md",
        chunk_level=ChunkLevel.DOCUMENT_SUMMARY,
        heading_path=[],
        start_page=1,
        end_page=3,
        text="Planning overview.",
        source_parent_id=None,
        source_content_hash="a" * 64,
        summary_model="model-v1",
        prompt_version="prompt-v1",
        score=0.5,
    )

    messages = grounded_messages("Summarize the document.", [], [], [summary])
    payload = json.loads(messages[1]["content"])

    assert payload["planning_summaries_not_evidence"][0]["text"] == "Planning overview."
    assert payload["evidence"] == []
    assert "not citable evidence" in messages[0]["content"]

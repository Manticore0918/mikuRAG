import hashlib
import math
from collections import defaultdict
from collections.abc import Sequence
from enum import StrEnum

from app.config import Settings
from app.models import Document


class RolloutPhase(StrEnum):
    BASELINE = "phase_0_baseline"
    STRUCTURED_CHUNKING = "phase_1_structured_chunking"
    RETRIEVAL_EXPANSION = "phase_2_retrieval_expansion"
    EXTRACTION_QUALITY = "phase_3_extraction_quality"
    BROAD_QUESTIONS = "phase_4_broad_questions"
    REINDEXING = "phase_5_reindexing"


def configured_rollout_phase(
    settings: Settings,
    *,
    active_reindex_job: bool,
) -> RolloutPhase:
    if active_reindex_job:
        return RolloutPhase.REINDEXING
    if settings.summary_generation_enabled:
        return RolloutPhase.BROAD_QUESTIONS
    if settings.hierarchical_retrieval_enabled:
        return RolloutPhase.RETRIEVAL_EXPANSION
    if settings.chunking_version == "hierarchical_v1":
        return RolloutPhase.STRUCTURED_CHUNKING
    return RolloutPhase.BASELINE


def select_canary_documents(
    documents: Sequence[Document],
    percentage: int,
) -> list[Document]:
    if not 1 <= percentage <= 100:
        raise ValueError("Canary percentage must be between 1 and 100")
    if not documents:
        return []
    by_knowledge_base: defaultdict[object, list[Document]] = defaultdict(list)
    for document in documents:
        by_knowledge_base[document.knowledge_base_id].append(document)

    selected: list[Document] = []
    for knowledge_base_id in sorted(by_knowledge_base, key=str):
        group = by_knowledge_base[knowledge_base_id]
        selected_count = max(1, math.ceil(len(group) * percentage / 100))
        ranked = sorted(
            group,
            key=lambda document: (
                hashlib.sha256(document.id.bytes).digest(),
                document.id.bytes,
            ),
        )
        selected.extend(ranked[:selected_count])
    return selected

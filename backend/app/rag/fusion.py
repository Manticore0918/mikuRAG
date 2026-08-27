"""Reciprocal-rank fusion behind an explicit FusionStrategy boundary.

RRF is the safe score-scale-independent fusion baseline: it combines rank
positions rather than raw scores, so semantic similarities and BM25 scores do
not need to share a scale.
"""

import uuid
from collections import Counter
from dataclasses import replace
from typing import Protocol

from app.rag.retrieval_types import Candidate


class FusionStrategy(Protocol):
    def fuse(
        self,
        semantic: list[Candidate],
        lexical: list[Candidate],
        *,
        rrf_k: int,
        semantic_weight: float,
        lexical_weight: float,
        limit: int,
        max_per_document: int | None,
    ) -> list[Candidate]: ...


class RrfFusionStrategy:
    """Weighted reciprocal-rank fusion, mergeing candidates by chunk id.

    Each present leg contributes `weight / (rrf_k + rank)` to the fused score.
    A leg that produced no candidates contributes nothing, so single-leg modes
    fall out of the same code path with their natural ordering.
    """

    def fuse(
        self,
        semantic: list[Candidate],
        lexical: list[Candidate],
        *,
        rrf_k: int,
        semantic_weight: float,
        lexical_weight: float,
        limit: int,
        max_per_document: int | None,
    ) -> list[Candidate]:
        combined: dict[uuid.UUID, Candidate] = {}
        for rank, candidate in enumerate(semantic, start=1):
            current = combined.setdefault(candidate.chunk_id, replace(candidate))
            current.semantic_similarity = candidate.semantic_similarity
            current.fused_score += semantic_weight / (rrf_k + rank)
        for rank, candidate in enumerate(lexical, start=1):
            current = combined.setdefault(candidate.chunk_id, replace(candidate))
            current.lexical_score = candidate.lexical_score
            current.fused_score += lexical_weight / (rrf_k + rank)

        ranked = sorted(
            combined.values(),
            key=lambda item: (
                item.fused_score,
                item.semantic_similarity if item.semantic_similarity is not None else -2.0,
                item.lexical_score if item.lexical_score is not None else -1.0,
            ),
            reverse=True,
        )
        if max_per_document is None:
            return ranked[:limit]

        selected: list[Candidate] = []
        document_counts: Counter[uuid.UUID] = Counter()
        for candidate in ranked:
            if document_counts[candidate.document_id] >= max_per_document:
                continue
            selected.append(candidate)
            document_counts[candidate.document_id] += 1
            if len(selected) == limit:
                break
        return selected

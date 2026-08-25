import re
from dataclasses import replace
from typing import Protocol

from app.rag.retrieval_types import Candidate

_TERM = re.compile(r"\w+", re.UNICODE)


class Reranker(Protocol):
    async def rerank(
        self,
        query: str,
        candidates: list[Candidate],
    ) -> list[Candidate]: ...


class DeterministicReranker:
    async def rerank(
        self,
        query: str,
        candidates: list[Candidate],
    ) -> list[Candidate]:
        query_terms = {term.casefold() for term in _TERM.findall(query)}
        reranked: list[Candidate] = []
        for candidate in candidates:
            searchable = " ".join([*candidate.heading_path, candidate.text])
            candidate_terms = {term.casefold() for term in _TERM.findall(searchable)}
            lexical_overlap = (
                len(query_terms & candidate_terms) / len(query_terms) if query_terms else 0.0
            )
            semantic = max(candidate.semantic_similarity or 0.0, 0.0)
            lexical = max(candidate.lexical_score or 0.0, 0.0)
            score = (
                candidate.fused_score
                + lexical_overlap * 0.04
                + semantic * 0.01
                + min(lexical, 1.0) * 0.005
            )
            reranked.append(replace(candidate, rerank_score=score))
        return sorted(
            reranked,
            key=lambda candidate: (
                candidate.effective_score,
                candidate.fused_score,
                candidate.semantic_similarity or -2.0,
                candidate.lexical_score or -1.0,
            ),
            reverse=True,
        )

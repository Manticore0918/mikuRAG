import re
import uuid
from collections import Counter
from dataclasses import replace

from app.ingestion.tokenization import Tokenizer
from app.rag.retrieval_types import Candidate

_NONSPACE = re.compile(r"\S+")
_TERMINAL_PUNCTUATION = re.compile(r"[.!?。！？)\]\"']$")


def apply_adaptive_diversity(
    candidates: list[Candidate],
    *,
    limit: int,
    document_penalty: float,
) -> list[Candidate]:
    deduplicated = suppress_duplicates(candidates)
    remaining = list(deduplicated)
    selected: list[Candidate] = []
    document_counts: Counter[uuid.UUID] = Counter()
    parent_counts: Counter[tuple[uuid.UUID, uuid.UUID]] = Counter()
    while remaining and len(selected) < limit:
        best = max(
            remaining,
            key=lambda candidate: _diversity_score(
                candidate,
                document_counts,
                parent_counts,
                document_penalty,
            ),
        )
        remaining.remove(best)
        selected.append(best)
        document_counts[best.document_id] += 1
        parent_counts[_parent_key(best)] += 1
    return selected


def suppress_duplicates(candidates: list[Candidate]) -> list[Candidate]:
    retained: list[Candidate] = []
    hashes: set[tuple[uuid.UUID, str]] = set()
    for candidate in candidates:
        if candidate.content_hash:
            key = (candidate.document_id, candidate.content_hash)
            if key in hashes:
                continue
            hashes.add(key)
        if any(_highly_overlapping(candidate, existing) for existing in retained):
            continue
        retained.append(candidate)
    return retained


def expansion_direction(candidate: Candidate) -> int:
    text = candidate.text.strip()
    if (
        not text
        or candidate.parent_chunk_id is None
        or candidate.content_type in {"table", "code"}
    ):
        return 0
    if not _TERMINAL_PUNCTUATION.search(text):
        return 1
    first = text[0]
    if first.islower() or first in {",", ";", ":", ")", "]"}:
        return -1
    return 0


def merge_adjacent_candidates(
    candidates: list[Candidate],
    *,
    tokenizer: Tokenizer,
    max_tokens: int,
) -> list[Candidate]:
    ranked = sorted(candidates, key=lambda candidate: candidate.effective_score, reverse=True)
    consumed: set[uuid.UUID] = set()
    merged: list[Candidate] = []
    for seed in ranked:
        if seed.chunk_id in consumed:
            continue
        same_parent = sorted(
            [
                candidate
                for candidate in candidates
                if candidate.chunk_id not in consumed
                and _same_merge_group(seed, candidate)
            ],
            key=lambda candidate: candidate.ordinal,
        )
        seed_index = next(
            (
                index
                for index, candidate in enumerate(same_parent)
                if candidate.chunk_id == seed.chunk_id
            ),
            0,
        )
        start = seed_index
        end = seed_index + 1
        while (
            start > 0
            and same_parent[start].ordinal - same_parent[start - 1].ordinal <= 1
        ):
            start -= 1
        while (
            end < len(same_parent)
            and same_parent[end].ordinal - same_parent[end - 1].ordinal <= 1
        ):
            end += 1
        compatible = same_parent[start:end]
        passage = compatible[0] if compatible else seed
        consumed.add(passage.chunk_id)
        for candidate in compatible[1:]:
            proposed_text = _merge_text(passage.text, candidate.text)
            if tokenizer.count(proposed_text) > max_tokens:
                break
            passage = _merged_candidate(passage, candidate, proposed_text, tokenizer)
            consumed.add(candidate.chunk_id)
        merged.append(passage)
    return sorted(merged, key=lambda candidate: candidate.effective_score, reverse=True)


def assemble_evidence_candidates(
    candidates: list[Candidate],
    *,
    tokenizer: Tokenizer,
    max_items: int,
    token_budget: int,
) -> tuple[list[Candidate], Counter[str]]:
    selected: list[Candidate] = []
    drops: Counter[str] = Counter()
    used_tokens = 0
    deduplicated = suppress_duplicates(candidates)
    duplicate_count = len(candidates) - len(deduplicated)
    if duplicate_count:
        drops["duplication"] += duplicate_count
    for candidate in deduplicated:
        if len(selected) >= max_items:
            drops["item_limit"] += 1
            continue
        tokens = candidate.token_count or tokenizer.count(candidate.text)
        if used_tokens + tokens > token_budget:
            drops["token_budget"] += 1
            continue
        selected.append(candidate)
        used_tokens += tokens
    return selected, drops


def _diversity_score(
    candidate: Candidate,
    document_counts: Counter[uuid.UUID],
    parent_counts: Counter[tuple[uuid.UUID, uuid.UUID]],
    penalty: float,
) -> float:
    document_count = document_counts[candidate.document_id]
    parent_count = parent_counts[_parent_key(candidate)]
    divisor = 1.0 + penalty * document_count + penalty * 0.5 * parent_count
    return candidate.effective_score / divisor


def _highly_overlapping(left: Candidate, right: Candidate) -> bool:
    if (
        left.document_id != right.document_id
        or left.start_offset is None
        or left.end_offset is None
        or right.start_offset is None
        or right.end_offset is None
    ):
        return False
    overlap = max(
        0,
        min(left.end_offset, right.end_offset) - max(left.start_offset, right.start_offset),
    )
    shorter = min(left.end_offset - left.start_offset, right.end_offset - right.start_offset)
    return shorter > 0 and overlap / shorter >= 0.9


def _same_merge_group(seed: Candidate, candidate: Candidate) -> bool:
    return (
        seed.document_id == candidate.document_id
        and seed.parent_chunk_id is not None
        and seed.parent_chunk_id == candidate.parent_chunk_id
        and seed.chunk_level == candidate.chunk_level == "child"
    )


def _merge_text(left: str, right: str) -> str:
    left_tokens = list(_NONSPACE.finditer(left))
    right_tokens = list(_NONSPACE.finditer(right))
    maximum = min(len(left_tokens), len(right_tokens), 256)
    overlap = 0
    left_values = [match.group(0) for match in left_tokens]
    right_values = [match.group(0) for match in right_tokens]
    for size in range(maximum, 0, -1):
        if left_values[-size:] == right_values[:size]:
            overlap = size
            break
    if overlap == 0:
        return f"{left}\n\n{right}"
    remainder_start = right_tokens[overlap - 1].end()
    remainder = right[remainder_start:].lstrip()
    return left if not remainder else f"{left} {remainder}"


def _merged_candidate(
    left: Candidate,
    right: Candidate,
    text: str,
    tokenizer: Tokenizer,
) -> Candidate:
    start_page = _minimum_optional(left.start_page, right.start_page)
    end_page = _maximum_optional(left.end_page, right.end_page)
    start_offset = _minimum_optional(left.start_offset, right.start_offset)
    end_offset = _maximum_optional(left.end_offset, right.end_offset)
    locator = dict(left.locator)
    if start_page is not None:
        locator["start_page"] = start_page
    if end_page is not None:
        locator["end_page"] = end_page
    if start_page is not None and start_page == end_page:
        locator["page"] = start_page
    else:
        locator.pop("page", None)
    source_ids = (
        left.source_chunk_ids or (left.chunk_id,),
        right.source_chunk_ids or (right.chunk_id,),
    )
    return replace(
        left,
        locator=locator,
        text=text,
        ordinal=min(left.ordinal, right.ordinal),
        start_page=start_page,
        end_page=end_page,
        start_offset=start_offset,
        end_offset=end_offset,
        token_count=tokenizer.count(text),
        content_hash=None,
        semantic_similarity=_maximum_optional(
            left.semantic_similarity,
            right.semantic_similarity,
        ),
        lexical_score=_maximum_optional(left.lexical_score, right.lexical_score),
        fused_score=max(left.fused_score, right.fused_score),
        rerank_score=max(left.effective_score, right.effective_score),
        source_chunk_ids=tuple(dict.fromkeys([*source_ids[0], *source_ids[1]])),
    )


def _minimum_optional(*values):
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _maximum_optional(*values):
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _parent_key(candidate: Candidate) -> tuple[uuid.UUID, uuid.UUID]:
    return candidate.document_id, candidate.parent_chunk_id or candidate.chunk_id

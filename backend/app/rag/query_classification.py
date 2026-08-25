import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class QueryKind(StrEnum):
    NARROW_FACT = "narrow_fact"
    MULTI_PART = "multi_part"
    BROAD = "broad"


@dataclass(frozen=True)
class QueryClassification:
    kind: QueryKind
    signals: tuple[str, ...] = ()


class QueryClassifier(Protocol):
    def classify(self, query: str) -> QueryClassification: ...


_BROAD_PATTERNS = {
    "summary": re.compile(
        r"\b(?:summari[sz]e|summary|overview|synopsis|key themes?|main points?)\b",
        re.IGNORECASE,
    ),
    "exhaustive": re.compile(
        r"\b(?:exhaustive|comprehensive|everything|all sections?|entire|whole)\b"
        r"|\ball (?:requirements?|policies|rules|topics|items|provisions)\b",
        re.IGNORECASE,
    ),
    "coverage": re.compile(
        r"\b(?:cover|walk me through|across (?:the )?(?:document|documents|knowledge base))\b"
        r"|\b(?:explain|describe) (?:the )?(?:document|documents|knowledge base)\b",
        re.IGNORECASE,
    ),
}
_COMPARATIVE = re.compile(
    r"\b(?:compare|contrast|versus|vs\.?|differences?|similarities?|between)\b",
    re.IGNORECASE,
)
_ENUMERATED_PART = re.compile(r"(?:^|\s)(?:\d+[.)]|[a-z][.)])\s+", re.IGNORECASE)
_QUESTION_WORD = re.compile(r"\b(?:what|which|who|when|where|why|how)\b", re.IGNORECASE)


class DeterministicQueryClassifier:
    """Conservative, replaceable rules for selecting a retrieval strategy."""

    def classify(self, query: str) -> QueryClassification:
        normalized = " ".join(query.split())
        broad_signals = tuple(
            name for name, pattern in _BROAD_PATTERNS.items() if pattern.search(normalized)
        )
        if broad_signals:
            return QueryClassification(QueryKind.BROAD, broad_signals)

        multi_signals: list[str] = []
        if _COMPARATIVE.search(normalized):
            multi_signals.append("comparative")
        if normalized.count("?") >= 2:
            multi_signals.append("multiple_questions")
        if len(_ENUMERATED_PART.findall(normalized)) >= 2:
            multi_signals.append("enumerated_parts")
        if len(_QUESTION_WORD.findall(normalized)) >= 2 and re.search(
            r"\b(?:and|also|while)\b", normalized, re.IGNORECASE
        ):
            multi_signals.append("coordinated_questions")
        if multi_signals:
            return QueryClassification(QueryKind.MULTI_PART, tuple(multi_signals))

        return QueryClassification(QueryKind.NARROW_FACT)

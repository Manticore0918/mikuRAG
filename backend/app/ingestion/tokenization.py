import re
from dataclasses import dataclass
from typing import Protocol

_TOKEN_CANDIDATE = re.compile(r"[A-Za-z0-9_]+|[^\s]")
_ASCII_TOKEN_WIDTH = 4


class Tokenizer(Protocol):
    name: str

    def count(self, text: str) -> int: ...

    def split(self, text: str, max_tokens: int) -> list[str]: ...

    def tail(self, text: str, max_tokens: int) -> str: ...


@dataclass(frozen=True)
class ConservativeTokenizer:
    """Dependency-free approximation for embedding-token limits.

    ASCII words are counted in four-character pieces. Non-ASCII characters and
    punctuation count individually, which deliberately errs on the safe side for
    multilingual documents.
    """

    name: str = "conservative_v1"

    def count(self, text: str) -> int:
        return len(_token_spans(text))

    def split(self, text: str, max_tokens: int) -> list[str]:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        spans = _token_spans(text)
        if not spans:
            return []
        pieces: list[str] = []
        for start_index in range(0, len(spans), max_tokens):
            group = spans[start_index : start_index + max_tokens]
            start = group[0][0]
            end = group[-1][1]
            piece = text[start:end].strip()
            if piece:
                pieces.append(piece)
        return pieces

    def tail(self, text: str, max_tokens: int) -> str:
        if max_tokens <= 0:
            return ""
        spans = _token_spans(text)
        if not spans:
            return ""
        start = spans[max(0, len(spans) - max_tokens)][0]
        return text[start:].strip()


def create_tokenizer(name: str) -> Tokenizer:
    if name == "conservative_v1":
        return ConservativeTokenizer()
    raise ValueError(f"Unsupported chunk tokenizer: {name}")


def _token_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _TOKEN_CANDIDATE.finditer(text):
        value = match.group(0)
        if value.isascii() and (value.isalnum() or "_" in value):
            for offset in range(0, len(value), _ASCII_TOKEN_WIDTH):
                spans.append(
                    (
                        match.start() + offset,
                        min(match.start() + offset + _ASCII_TOKEN_WIDTH, match.end()),
                    )
                )
        else:
            spans.append((match.start(), match.end()))
    return spans

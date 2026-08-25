from dataclasses import dataclass

from app.ingestion.contracts import ExtractedSection


@dataclass(frozen=True)
class TextChunk:
    text: str
    locator: dict[str, int | str]


def _boundary(text: str, start: int, target: int) -> int:
    proposed = min(len(text), start + target)
    if proposed == len(text):
        return proposed
    lower = start + max(target // 2, 1)
    candidates = [
        text.rfind("\n\n", lower, proposed),
        text.rfind(". ", lower, proposed),
        text.rfind("\n", lower, proposed),
        text.rfind(" ", lower, proposed),
    ]
    best = max(candidates)
    return proposed if best < lower else best + 1


def chunk_sections(
    sections: list[ExtractedSection],
    target_characters: int,
    overlap_characters: int,
) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for section in sections:
        text = section.text.strip()
        start = 0
        part = 1
        while start < len(text):
            end = _boundary(text, start, target_characters)
            chunk_text = text[start:end].strip()
            if chunk_text:
                locator = dict(section.locator)
                locator["part"] = part
                chunks.append(TextChunk(text=chunk_text, locator=locator))
                part += 1
            if end >= len(text):
                break
            next_start = max(start + 1, end - overlap_characters)
            while next_start < end and not text[next_start].isspace():
                next_start += 1
            start = min(next_start, end)
    return chunks

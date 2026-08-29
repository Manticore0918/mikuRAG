"""Deterministic model-provider stub for the CI compose smoke test.

Runs on the standard library only (it is executed inside a bare
python:3.12-slim container). It implements the two provider contracts the
backend consumes:

- the DashScope-style embedding endpoint: deterministic 768-dimensional
  vectors hashed from character trigrams, so lexically related text really
  ranks higher and the smoke test can exercise ranking;
- the OpenAI-compatible chat-completions endpoint (streaming and
  non-streaming): returns the JSON payloads the grounded pipeline validates
  (query rewrites echo the current question; grounded answers cite exactly
  the evidence IDs the server supplied).

Everything is deterministic and no content is persisted.
"""

import hashlib
import json
import math
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

EMBEDDING_DIMENSION = 768
STUB_PORT = 8900


def _embedding_for(text: str) -> list[float]:
    """Hash normalized character trigrams into a unit vector.

    Shared substrings map to shared dimensions, so cosine similarity tracks
    textual overlap without any model or randomness.
    """

    normalized = " ".join(text.casefold().split())
    padded = f"  {normalized}  "
    vector = [0.0] * EMBEDDING_DIMENSION
    for index in range(len(padded) - 2):
        trigram = padded[index : index + 3]
        digest = hashlib.sha256(trigram.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSION
        vector[bucket] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _last_user_content(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
    return ""


def _evidence_ids(content: str) -> list[str]:
    try:
        payload = json.loads(content)
    except ValueError:
        return []
    evidence = payload.get("evidence") if isinstance(payload, dict) else None
    if not isinstance(evidence, list):
        return []
    identifiers: list[str] = []
    for item in evidence:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            identifiers.append(item["id"])
    return identifiers


def _current_question(content: str) -> str | None:
    try:
        payload = json.loads(content)
    except ValueError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("current_question"), str):
        return payload["current_question"]
    return None


def _answer_payload(content: str) -> dict[str, Any]:
    identifiers = _evidence_ids(content)
    if not identifiers:
        return {"status": "insufficient", "claims": []}
    claims = [
        {
            "text": "The retrieved Documents support the requested information.",
            "evidence_ids": identifiers[:1],
        }
    ]
    for identifier in identifiers[1:5]:
        claims.append(
            {
                "text": "Additional retrieved evidence confirms the same details.",
                "evidence_ids": [identifier],
            }
        )
    return {"status": "answer", "claims": claims}


def _rewrite_payload(content: str) -> dict[str, Any]:
    question = _current_question(content) or "smoke question"
    return {
        "query": question,
        "inferred_filters": {
            "tags": [],
            "source_kinds": [],
            "languages": [],
            "document_ids": [],
            "ingested_after": None,
            "ingested_before": None,
        },
        "preserved_identifiers": [],
    }


def _completion_content(messages: list[dict[str, Any]]) -> str:
    content = _last_user_content(messages)
    if _current_question(content) is not None and not _evidence_ids(content):
        payload = _rewrite_payload(content)
    else:
        payload = _answer_payload(content)
    return json.dumps(payload, ensure_ascii=False)


def _sse_chunks(text: str) -> list[str]:
    chunks = []
    for start in range(0, len(text), 200):
        piece = text[start : start + 200]
        chunks.append(
            json.dumps(
                {
                    "choices": [{"delta": {"content": piece}}],
                }
            )
        )
    chunks.append(
        json.dumps(
            {
                "choices": [{"delta": {}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 20, "total_tokens": 32},
            }
        )
    )
    return chunks


class ProviderStubHandler(BaseHTTPRequestHandler):
    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.endswith("/healthz"):
            self._respond(200, b'{"status":"ok"}', "application/json")
            return
        self._respond(404, b'{"error":"not found"}', "application/json")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {}

        if "/chat/completions" in self.path:
            messages = payload.get("messages") if isinstance(payload, dict) else None
            if not isinstance(messages, list):
                messages = []
            content = _completion_content(messages)
            if payload.get("stream"):
                body = "".join(
                    f"data: {chunk}\n\n" for chunk in _sse_chunks(content)
                ).encode("utf-8") + b"data: [DONE]\n\n"
                self._respond(200, body, "text/event-stream")
            else:
                body = json.dumps(
                    {
                        "choices": [{"message": {"role": "assistant", "content": content}}],
                        "usage": {
                            "prompt_tokens": 12,
                            "completion_tokens": 20,
                            "total_tokens": 32,
                        },
                    }
                ).encode("utf-8")
                self._respond(200, body, "application/json")
            return

        # DashScope-style multimodal embedding request.
        contents = (
            payload.get("input", {}).get("contents", []) if isinstance(payload, dict) else []
        )
        texts = [
            item.get("text", "") if isinstance(item, dict) else "" for item in contents
        ]
        embeddings = [
            {"index": index, "embedding": _embedding_for(text)}
            for index, text in enumerate(texts)
        ]
        body = json.dumps({"output": {"embeddings": embeddings}}).encode("utf-8")
        self._respond(200, body, "application/json")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        print(f"provider-stub: {format % args}")


def main() -> None:
    server = HTTPServer(("0.0.0.0", STUB_PORT), ProviderStubHandler)
    print(f"provider-stub listening on :{STUB_PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

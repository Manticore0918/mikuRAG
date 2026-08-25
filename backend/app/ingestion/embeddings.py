import math
from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter

import httpx

from app.config import Settings, get_settings
from app.ingestion.errors import EmbeddingProviderError

EMBEDDING_DIMENSION = 768


@dataclass
class EmbeddingMetrics:
    request_count: int = 0
    input_count: int = 0
    duration_ms: float = 0.0


def parse_embeddings(payload: object, expected_count: int) -> list[list[float]]:
    try:
        output = payload["output"]  # type: ignore[index]
        raw_embeddings = output["embeddings"]  # type: ignore[index]
        ordered = sorted(raw_embeddings, key=lambda item: item["index"])
        indexes = [item["index"] for item in ordered]
        vectors = [item["embedding"] for item in ordered]
    except (KeyError, TypeError) as error:
        raise EmbeddingProviderError(
            "The embedding provider returned an invalid response"
        ) from error
    if len(vectors) != expected_count or indexes != list(range(expected_count)):
        raise EmbeddingProviderError("The embedding provider returned an incomplete response")
    for vector in vectors:
        if len(vector) != EMBEDDING_DIMENSION or not all(
            isinstance(value, (int, float)) and math.isfinite(value) for value in vector
        ):
            raise EmbeddingProviderError("The embedding provider returned invalid vectors")
    return [[float(value) for value in vector] for vector in vectors]


async def embed_texts(
    texts: Sequence[str],
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    *,
    metrics: EmbeddingMetrics | None = None,
) -> list[list[float]]:
    active_settings = settings or get_settings()
    if active_settings.embedding_api_key is None:
        raise EmbeddingProviderError("The embedding provider is not configured")
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=active_settings.embedding_timeout_seconds)
    vectors: list[list[float]] = []
    try:
        for start in range(0, len(texts), active_settings.embedding_batch_size):
            batch = texts[start : start + active_settings.embedding_batch_size]
            request_started = perf_counter()
            if metrics is not None:
                metrics.request_count += 1
                metrics.input_count += len(batch)
            try:
                response = await http_client.post(
                    active_settings.embedding_endpoint,
                    headers={
                        "Authorization": (
                            f"Bearer {active_settings.embedding_api_key.get_secret_value()}"
                        ),
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": active_settings.embedding_model_id,
                        "input": {"contents": [{"text": text} for text in batch]},
                    },
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as error:
                raise EmbeddingProviderError(
                    "The embedding provider is unavailable or rejected the request"
                ) from error
            finally:
                if metrics is not None:
                    metrics.duration_ms += (perf_counter() - request_started) * 1_000
            vectors.extend(parse_embeddings(payload, len(batch)))
    finally:
        if owns_client:
            await http_client.aclose()
    return vectors

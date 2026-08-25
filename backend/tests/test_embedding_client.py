import httpx
import pytest

from app.config import Settings
from app.ingestion.embeddings import (
    EMBEDDING_DIMENSION,
    EmbeddingMetrics,
    embed_texts,
    parse_embeddings,
)
from app.ingestion.errors import EmbeddingProviderError


def vector(seed: float) -> list[float]:
    return [seed] * EMBEDDING_DIMENSION


def settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://user:pass@postgres:5432/mikurag",
        redis_url="redis://redis:6379/0",
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        embedding_endpoint="https://example.test/embed",
        embedding_api_key="provider-secret",
        embedding_batch_size=2,
    )


def test_embedding_response_is_ordered_by_provider_index() -> None:
    payload = {
        "output": {
            "embeddings": [
                {"index": 1, "embedding": vector(2.0)},
                {"index": 0, "embedding": vector(1.0)},
            ]
        }
    }
    assert parse_embeddings(payload, 2)[0][0] == 1.0


def test_embedding_response_rejects_wrong_vector_dimension() -> None:
    with pytest.raises(EmbeddingProviderError, match="invalid vectors"):
        parse_embeddings(
            {"output": {"embeddings": [{"index": 0, "embedding": [1.0]}]}},
            1,
        )


@pytest.mark.asyncio
async def test_embedding_client_batches_native_model_studio_requests() -> None:
    request_sizes: list[int] = []
    metrics = EmbeddingMetrics()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer provider-secret"
        payload = __import__("json").loads(request.content)
        assert payload["model"] == "tongyi-embedding-vision-flash-2026-03-06"
        contents = payload["input"]["contents"]
        request_sizes.append(len(contents))
        return httpx.Response(
            200,
            json={
                "output": {
                    "embeddings": [
                        {"index": index, "embedding": vector(float(index))}
                        for index in range(len(contents))
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await embed_texts(
            ["one", "two", "three"],
            settings(),
            client,
            metrics=metrics,
        )

    assert request_sizes == [2, 1]
    assert len(result) == 3
    assert metrics.request_count == 2
    assert metrics.input_count == 3
    assert metrics.duration_ms >= 0

import json

import httpx
import pytest

from app.config import Settings
from app.rag.generation import GenerationProviderError, complete_json, stream_json


def settings() -> Settings:
    return Settings(
        session_secret="s" * 32,
        encryption_master_key="e" * 32,
        generation_base_url="http://localhost:11434/v1",
    )


@pytest.mark.asyncio
async def test_openai_compatible_completion_ignores_thinking_block() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert body["reasoning_effort"] == "none"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "<think>private</think>{\"query\":\"policy\"}"
                        }
                    }
                ],
                "usage": {"prompt_tokens": 4, "total_tokens": 7},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await complete_json([{"role": "user", "content": "question"}], settings(), client)

    assert result.payload == {"query": "policy"}
    assert result.usage == {"prompt_tokens": 4, "total_tokens": 7}


@pytest.mark.asyncio
async def test_stream_collects_only_visible_content() -> None:
    events = [
        {"choices": [{"delta": {"reasoning": "hidden"}}]},
        {"choices": [{"delta": {"content": "{\"status\":\"insufficient\","}}]},
        {"choices": [{"delta": {"content": "\"claims\":[]}"}}], "usage": {"total_tokens": 9}},
    ]
    content = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=content, headers={"Content-Type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await stream_json([{"role": "user", "content": "question"}], settings(), client)

    assert result.payload == {"status": "insufficient", "claims": []}
    assert result.usage == {"total_tokens": 9}


@pytest.mark.asyncio
async def test_invalid_provider_payload_raises_safe_error() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GenerationProviderError, match="local generation provider"):
            await complete_json([], settings(), client)

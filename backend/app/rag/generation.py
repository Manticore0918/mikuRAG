import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings, get_settings

THINKING_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
MAX_GENERATION_CHARACTERS = 100_000


class GenerationProviderError(Exception):
    def __init__(self, safe_message: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


@dataclass(frozen=True)
class GenerationResult:
    payload: dict[str, Any]
    usage: dict[str, int]


def parse_json_content(content: str) -> dict[str, Any]:
    cleaned = THINKING_BLOCK.sub("", content).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < start:
        raise GenerationProviderError("The local generation model returned invalid JSON")
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as error:
        raise GenerationProviderError("The local generation model returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise GenerationProviderError("The local generation model returned invalid JSON")
    return payload


def _headers(settings: Settings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.generation_api_key is not None:
        headers["Authorization"] = (
            f"Bearer {settings.generation_api_key.get_secret_value()}"
        )
    return headers


def _request_body(
    messages: list[dict[str, str]],
    settings: Settings,
    *,
    stream: bool,
) -> dict[str, Any]:
    return {
        "model": settings.generation_model_id,
        "messages": messages,
        "stream": stream,
        "temperature": 0.0,
        "max_tokens": settings.generation_max_tokens,
        "reasoning_effort": "none",
        "response_format": {"type": "json_object"},
    }


def _usage(payload: object) -> dict[str, int]:
    if not isinstance(payload, dict):
        return {}
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}
    return {
        key: int(value)
        for key, value in usage.items()
        if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
        and isinstance(value, int)
    }


async def complete_json(
    messages: list[dict[str, str]],
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> GenerationResult:
    active_settings = settings or get_settings()
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=active_settings.generation_timeout_seconds)
    try:
        try:
            response = await http_client.post(
                f"{active_settings.generation_base_url}/chat/completions",
                headers=_headers(active_settings),
                json=_request_body(messages, active_settings, stream=False),
            )
            response.raise_for_status()
            response_payload = response.json()
            content = response_payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("content is not text")
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise GenerationProviderError(
                "The local generation provider is unavailable or returned an invalid response"
            ) from error
        return GenerationResult(
            payload=parse_json_content(content),
            usage=_usage(response_payload),
        )
    finally:
        if owns_client:
            await http_client.aclose()


async def stream_json(
    messages: list[dict[str, str]],
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> GenerationResult:
    active_settings = settings or get_settings()
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=active_settings.generation_timeout_seconds)
    pieces: list[str] = []
    usage: dict[str, int] = {}
    try:
        try:
            async with http_client.stream(
                "POST",
                f"{active_settings.generation_base_url}/chat/completions",
                headers=_headers(active_settings),
                json=_request_body(messages, active_settings, stream=True),
            ) as response:
                response.raise_for_status()
                character_count = 0
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    event = json.loads(line)
                    if isinstance(event, dict) and event.get("error"):
                        raise GenerationProviderError(
                            "The local generation provider failed during generation"
                        )
                    delta = event["choices"][0].get("delta", {})
                    content = delta.get("content")
                    if isinstance(content, str):
                        character_count += len(content)
                        if character_count > MAX_GENERATION_CHARACTERS:
                            raise GenerationProviderError(
                                "The local generation response exceeded the safe limit"
                            )
                        pieces.append(content)
                    usage.update(_usage(event))
        except GenerationProviderError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise GenerationProviderError(
                "The local generation provider is unavailable or returned an invalid response"
            ) from error
        return GenerationResult(payload=parse_json_content("".join(pieces)), usage=usage)
    finally:
        if owns_client:
            await http_client.aclose()

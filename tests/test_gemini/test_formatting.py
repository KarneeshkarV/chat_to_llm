from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from providers.gemini.formatting import GeminiFormatter


async def _chunk_stream():
    yield SimpleNamespace(text_delta="Hello")
    yield SimpleNamespace(text_delta=" world")


def test_stream_response_to_openai_chunks():
    async def collect():
        formatter = GeminiFormatter()
        return [
            chunk
            async for chunk in formatter.stream_response(
                service=None,
                response=_chunk_stream(),
                model="gemini-2.5-flash",
                max_tokens=100,
            )
        ]

    chunks = asyncio.run(collect())
    assert chunks[0].startswith("data: ")
    first_payload = json.loads(chunks[0][6:])
    assert first_payload["choices"][0]["delta"]["role"] == "assistant"

    second_payload = json.loads(chunks[1][6:])
    assert second_payload["choices"][0]["delta"]["content"] == "Hello"

    third_payload = json.loads(chunks[2][6:])
    assert third_payload["choices"][0]["delta"]["content"] == " world"
    assert chunks[-1] == "data: [DONE]\n\n"


def test_format_not_stream_response_collects_text():
    async def collect():
        formatter = GeminiFormatter()
        return await formatter.format_not_stream_response(
            formatter.stream_response(
                service=None,
                response=_chunk_stream(),
                model="gemini-2.5-flash",
                max_tokens=100,
            ),
            prompt_tokens=12,
            max_tokens=100,
            model="gemini-2.5-flash",
        )

    response = asyncio.run(collect())
    assert response["object"] == "chat.completion"
    assert response["choices"][0]["message"]["content"] == "Hello world"
    assert response["usage"]["prompt_tokens"] == 12


def test_stream_response_can_append_final_json_event():
    async def collect():
        formatter = GeminiFormatter()
        service = SimpleNamespace(data={"stream_final_json": True}, prompt_tokens=12)
        return [
            chunk
            async for chunk in formatter.stream_response(
                service=service,
                response=_chunk_stream(),
                model="gemini-3-flash",
                max_tokens=100,
            )
        ]

    chunks = asyncio.run(collect())
    final_json_event = chunks[-2]
    assert final_json_event.startswith("event: response.completed\n")
    assert "\ndata: " in final_json_event
    payload = json.loads(final_json_event.split("\ndata: ", 1)[1].strip())
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "Hello world"
    assert payload["usage"]["prompt_tokens"] == 12
    assert chunks[-1] == "data: [DONE]\n\n"

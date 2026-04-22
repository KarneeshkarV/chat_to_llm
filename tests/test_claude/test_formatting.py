from __future__ import annotations

import asyncio
import json

from providers.claude.formatting import ClaudeFormatter


async def _line_stream():
    yield "event: completion"
    yield 'data: {"completion":"Hello"}'
    yield ""
    yield 'data: {"completion":" world"}'
    yield ""


def test_stream_response_to_openai_chunks():
    async def collect():
        formatter = ClaudeFormatter()
        return [
            chunk
            async for chunk in formatter.stream_response(
                service=None,
                response=_line_stream(),
                model="claude-3-5-sonnet-latest",
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
        formatter = ClaudeFormatter()
        return await formatter.format_not_stream_response(
            formatter.stream_response(
                service=None,
                response=_line_stream(),
                model="claude-3-5-sonnet-latest",
                max_tokens=100,
            ),
            prompt_tokens=12,
            max_tokens=100,
            model="claude-3-5-sonnet-latest",
        )

    response = asyncio.run(collect())
    assert response["object"] == "chat.completion"
    assert response["choices"][0]["message"]["content"] == "Hello world"
    assert response["usage"]["prompt_tokens"] == 12

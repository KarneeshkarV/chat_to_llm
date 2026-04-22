from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncGenerator

from providers.base import BaseFormatter

logger = logging.getLogger(__name__)


class GrokFormatter(BaseFormatter):
    """Formatter for Grok responses.

    Grok currently only supports non-streaming responses. The actual
    OpenAI-compatible dict is built inside GrokService.send_conversation.
    """

    async def stream_response(
        self, service: Any, response: Any, model: str, max_tokens: int
    ) -> AsyncGenerator[str, None]:
        yield "data: %s\n\n" % json.dumps(
            {
                "id": "chatcmpl-grok",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": ""},
                        "finish_reason": None,
                    }
                ],
            }
        )
        yield "data: %s\n\n" % json.dumps(
            {
                "id": "chatcmpl-grok",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": response},
                        "finish_reason": "stop",
                    }
                ],
            }
        )
        yield "data: [DONE]\n\n"

    async def format_not_stream_response(
        self,
        response: AsyncGenerator,
        prompt_tokens: int,
        max_tokens: int,
        model: str,
    ) -> dict:
        all_text = ""
        async for chunk in response:
            try:
                if chunk.startswith("data: [DONE]"):
                    break
                if not chunk.startswith("data: "):
                    continue
                chunk_data = json.loads(chunk[6:])
                delta = chunk_data.get("choices", [{}])[0].get("delta")
                if delta:
                    all_text += delta.get("content", "")
            except Exception as exc:
                logger.error("Non-stream collect error: %s", exc)
                continue

        completion_tokens = len(all_text.split()) if all_text else 0
        return {
            "id": "chatcmpl-grok",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": all_text},
                    "logprobs": None,
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

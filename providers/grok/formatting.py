from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncGenerator

from providers.base import BaseFormatter

logger = logging.getLogger(__name__)


class GrokFormatter(BaseFormatter):
    async def stream_response(
        self, service: Any, response: Any, model: str, max_tokens: int
    ) -> AsyncGenerator[str, None]:
        yield "data: %s\n\n" % json.dumps(
            {
                "id": "chatcmpl-grok-stub",
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
                "id": "chatcmpl-grok-stub",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "Grok chat is not yet implemented."},
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
        return {
            "id": "chatcmpl-grok-stub",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Grok chat is not yet implemented.",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 7,
                "total_tokens": prompt_tokens + 7,
            },
        }

from __future__ import annotations

import json
import logging
import random
import string
import time
from typing import Any, AsyncGenerator

from providers.base import BaseFormatter

logger = logging.getLogger(__name__)


def _generate_chat_id() -> str:
    return "chatcmpl-" + "".join(
        random.choice(string.ascii_letters + string.digits) for _ in range(29)
    )


def _approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


class GeminiFormatter(BaseFormatter):
    def build_response(
        self,
        text: str,
        prompt_tokens: int,
        max_tokens: int,
        model: str,
        finish_reason: str = "stop",
        response_id: str | None = None,
        created_time: int | None = None,
    ) -> dict:
        completion_tokens = min(_approx_tokens(text), max_tokens)
        return {
            "id": response_id or _generate_chat_id(),
            "object": "chat.completion",
            "created": created_time or int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "logprobs": None,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    async def stream_response(
        self, service: Any, response: Any, model: str, max_tokens: int
    ) -> AsyncGenerator[str, None]:
        chat_id = _generate_chat_id()
        created_time = int(time.time())
        accumulated = ""
        emit_final_json = bool(getattr(service, "data", {}).get("stream_final_json", False))
        prompt_tokens = int(getattr(service, "prompt_tokens", 0) or 0)

        yield "data: %s\n\n" % json.dumps(
            {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created_time,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": ""},
                        "logprobs": None,
                        "finish_reason": None,
                    }
                ],
            }
        )

        async for chunk in response:
            delta_text = getattr(chunk, "text_delta", None)
            if not delta_text:
                full_text = getattr(chunk, "text", None)
                if isinstance(full_text, str) and full_text:
                    delta_text = full_text[len(accumulated) :] if full_text.startswith(accumulated) else full_text

            if not delta_text:
                continue

            next_text = accumulated + delta_text
            finish_reason = None
            delta = {"content": delta_text}
            if _approx_tokens(next_text) >= max_tokens:
                delta = {}
                finish_reason = "length"

            yield "data: %s\n\n" % json.dumps(
                {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": delta,
                            "logprobs": None,
                            "finish_reason": finish_reason,
                        }
                    ],
                }
            )

            if finish_reason == "length":
                if emit_final_json:
                    final_response = self.build_response(
                        text=accumulated,
                        prompt_tokens=prompt_tokens,
                        max_tokens=max_tokens,
                        model=model,
                        finish_reason=finish_reason,
                        response_id=chat_id,
                        created_time=created_time,
                    )
                    yield "event: response.completed\ndata: %s\n\n" % json.dumps(final_response)
                yield "data: [DONE]\n\n"
                return

            accumulated = next_text

        yield "data: %s\n\n" % json.dumps(
            {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created_time,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "logprobs": None,
                        "finish_reason": "stop",
                    }
                ],
            }
        )
        if emit_final_json:
            final_response = self.build_response(
                text=accumulated,
                prompt_tokens=prompt_tokens,
                max_tokens=max_tokens,
                model=model,
                finish_reason="stop",
                response_id=chat_id,
                created_time=created_time,
            )
            yield "event: response.completed\ndata: %s\n\n" % json.dumps(final_response)
        yield "data: [DONE]\n\n"

    async def format_not_stream_response(
        self,
        response: AsyncGenerator,
        prompt_tokens: int,
        max_tokens: int,
        model: str,
    ) -> dict:
        all_text = ""
        finish_reason = "stop"

        async for chunk in response:
            try:
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8")
                if chunk.startswith("data: [DONE]"):
                    break
                if not chunk.startswith("data: "):
                    continue
                payload = json.loads(chunk[6:])
                choice = payload.get("choices", [{}])[0]
                delta = choice.get("delta") or {}
                if "content" in delta:
                    all_text += delta["content"]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
            except Exception as exc:
                logger.error("Gemini non-stream collect error: %s", exc)
                continue

        return self.build_response(
            text=all_text,
            prompt_tokens=prompt_tokens,
            max_tokens=max_tokens,
            model=model,
            finish_reason=finish_reason,
        )

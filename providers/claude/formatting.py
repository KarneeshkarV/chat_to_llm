from __future__ import annotations

import json
import logging
import random
import string
import time
from typing import Any, AsyncGenerator, AsyncIterator, Iterable, Optional

from fastapi import HTTPException

from providers.base import BaseFormatter

logger = logging.getLogger(__name__)


def _generate_chat_id() -> str:
    return "chatcmpl-" + "".join(
        random.choice(string.ascii_letters + string.digits) for _ in range(29)
    )


def _approx_completion_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


async def _iter_sse_events(response: AsyncIterator[str]) -> AsyncGenerator[list[str], None]:
    buffer: list[str] = []
    async for raw_line in response:
        line = (
            raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, bytes) else raw_line
        )
        line = line.rstrip("\r\n")
        if not line:
            if buffer:
                yield buffer
                buffer = []
            continue
        buffer.append(line)
    if buffer:
        yield buffer


def _event_data(lines: Iterable[str]) -> Optional[str]:
    chunks = []
    for line in lines:
        if not line.startswith("data:"):
            continue
        chunks.append(line[5:].lstrip())
    if not chunks:
        return None
    return "\n".join(chunks)


def _extract_text_from_event(data: dict) -> Optional[str]:
    # Legacy "raw" rendering: {"type":"completion","completion":" ...","stop_reason":...}
    completion = data.get("completion")
    if isinstance(completion, str) and completion:
        return completion

    event_type = data.get("type")

    # Structured "messages" rendering:
    #   {"type":"content_block_delta","delta":{"type":"text_delta","text":" ..."}}
    # Thinking deltas ({"type":"thinking_delta"}) are intentionally ignored —
    # they should not appear in the user-visible assistant message.
    if event_type == "content_block_delta":
        delta = data.get("delta") or {}
        if delta.get("type") == "text_delta":
            text = delta.get("text")
            if isinstance(text, str) and text:
                return text
        # Some variants put the text under "text" without a "type" tag.
        text = delta.get("text")
        if isinstance(text, str) and text and delta.get("type") in (None, "text_delta"):
            return text
        return None

    # Occasional shape: content_block_start carrying an initial chunk of text.
    if event_type == "content_block_start":
        block = data.get("content_block") or {}
        if block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                return text
    return None


def _error_from_event(data: dict) -> Optional[str]:
    if data.get("type") == "error":
        err = data.get("error") or {}
        if isinstance(err, dict):
            return err.get("message") or err.get("type") or json.dumps(err)[:300]
        return str(err)[:300]
    return None


class ClaudeRefusal(Exception):
    """Claude.ai's safety layer refused to answer (stop_reason='refusal')."""


def _is_refusal(data: dict) -> bool:
    return data.get("stop_reason") == "refusal"


async def _iter_completion_deltas(response: AsyncIterator[str]) -> AsyncGenerator[str, None]:
    async for event_lines in _iter_sse_events(response):
        payload = _event_data(event_lines)
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logger.debug("Skipping non-JSON Claude SSE payload: %s", payload[:200])
            continue

        err = _error_from_event(data)
        if err:
            raise HTTPException(status_code=502, detail="Claude: %s" % err)

        if _is_refusal(data):
            raise ClaudeRefusal()

        text = _extract_text_from_event(data)
        if text:
            yield text


class ClaudeFormatter(BaseFormatter):
    async def stream_response(
        self, service: Any, response: Any, model: str, max_tokens: int
    ) -> AsyncGenerator[str, None]:
        chat_id = _generate_chat_id()
        created_time = int(time.time())
        completion_tokens = 0

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

        async for delta_text in _iter_completion_deltas(response):
            completion_tokens += _approx_completion_tokens(delta_text)
            finish_reason = None
            delta = {"content": delta_text}
            if completion_tokens >= max_tokens:
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
                yield "data: [DONE]\n\n"
                return

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
        yield "data: [DONE]\n\n"

    async def format_not_stream_response(
        self,
        response: AsyncGenerator,
        prompt_tokens: int,
        max_tokens: int,
        model: str,
    ) -> dict:
        created_time = int(time.time())
        chat_id = _generate_chat_id()
        all_text = ""
        finish_reason = "stop"

        try:
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
                except HTTPException:
                    raise
                except Exception as exc:
                    logger.error("Claude non-stream collect error: %s", exc)
                    continue
        except ClaudeRefusal:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Claude.ai refused this request (stop_reason='refusal'). "
                    "The web interface blocks certain prompts — financial/trading "
                    "charts and advice are a common trigger. Try another provider "
                    "(Gemini / ChatGPT / Grok), rephrase the prompt to remove "
                    "trading-advice framing, or use the Anthropic API directly."
                ),
            )

        if not all_text:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Claude returned an empty response. This usually means the "
                    "model produced no content for the given input "
                    "(e.g. an unreadable image or a stalled completion stream). "
                    "Try a different image or prompt."
                ),
            )

        completion_tokens = min(_approx_completion_tokens(all_text), max_tokens)
        return {
            "id": chat_id,
            "object": "chat.completion",
            "created": created_time,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": all_text},
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

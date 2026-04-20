from __future__ import annotations

import json
import logging
import random
import string
import time
import uuid
from typing import Any, AsyncGenerator, List, Optional, Tuple

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_moderation_message = (
    "I'm sorry, I cannot provide or engage in any content related to pornography, "
    "violence, or any unethical material."
)


def _generate_chat_id() -> str:
    return "chatcmpl-" + "".join(
        random.choice(string.ascii_letters + string.digits) for _ in range(29)
    )


def _to_chat_message(role: str, content: Any) -> dict:
    if isinstance(content, list):
        parts: list = []
        attachments: list = []
        for item in content:
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif item.get("type") == "image_url":
                parts.append("[image: %s]" % item.get("image_url", {}).get("url", ""))
        return {
            "id": str(uuid.uuid4()),
            "author": {"role": role},
            "content": {"content_type": "multimodal_text", "parts": parts},
            "metadata": {"attachments": attachments},
        }
    return {
        "id": str(uuid.uuid4()),
        "author": {"role": role},
        "content": {"content_type": "text", "parts": [content or ""]},
        "metadata": {},
    }


def api_messages_to_chat(api_messages: List[dict]) -> List[dict]:
    chat_messages: List[dict] = []
    for msg in api_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            role = "system"
        chat_messages.append(_to_chat_message(role, content))
    return chat_messages


async def head_process_response(response: Any) -> Tuple[Any, bool]:
    async for chunk in response:
        chunk = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        if chunk.startswith("data: {"):
            try:
                chunk_data = json.loads(chunk[6:])
            except json.JSONDecodeError:
                continue
            message = chunk_data.get("message", {})
            if not message and "error" in chunk_data:
                return response, False
            role = message.get("author", {}).get("role")
            if role in ("user", "system"):
                continue
            status = message.get("status")
            if status == "in_progress":
                return response, True
    return response, False


async def stream_response(
    service: Any,
    response: Any,
    model: str,
    max_tokens: int,
) -> AsyncGenerator[str, None]:
    chat_id = _generate_chat_id()
    created_time = int(time.time())
    completion_tokens = 0
    len_last_content = 0
    len_last_citation = 0
    last_message_id: Optional[str] = None
    last_role: Optional[str] = None
    last_content_type: Optional[str] = None
    model_slug: Optional[str] = None
    end = False

    first_chunk = {
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
    yield "data: %s\n\n" % json.dumps(first_chunk)

    async for chunk in response:
        chunk = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        if end:
            yield "data: [DONE]\n\n"
            break

        if not chunk.startswith("data: {"):
            if chunk.startswith("data: [DONE]"):
                yield "data: [DONE]\n\n"
            continue

        try:
            chunk_data = json.loads(chunk[6:])
        except json.JSONDecodeError:
            continue

        try:
            finish_reason = None
            message = chunk_data.get("message", {})
            role = message.get("author", {}).get("role")
            if role in ("user", "system"):
                continue

            status = message.get("status")
            message_id = message.get("id")
            content = message.get("content", {})
            meta_data = message.get("metadata", {})
            initial_text = meta_data.get("initial_text", "")
            model_slug = meta_data.get("model_slug", model_slug)

            if not message and chunk_data.get("type") == "moderation":
                delta: dict = {"role": "assistant", "content": _moderation_message}
                finish_reason = "stop"
                end = True
            elif status == "in_progress":
                outer_content_type = content.get("content_type")
                new_text = ""
                if outer_content_type == "text":
                    part = content.get("parts", [])[0]
                    if not part:
                        if last_role != role and last_role is not None:
                            new_text = "\n"
                    else:
                        if last_message_id and last_message_id != message_id:
                            pass
                        else:
                            citation = message.get("metadata", {}).get("citations", [])
                            if len(citation) > len_last_citation:
                                inside = citation[-1].get("metadata", {})
                                title = inside.get("title", "")
                                url = inside.get("url", "")
                                new_text = ' **[[""]](%s "%s")** ' % (url, title)
                                len_last_citation = len(citation)
                            else:
                                if role == "assistant" and last_role != "assistant":
                                    if last_role is None:
                                        new_text = part[len_last_content:]
                                    else:
                                        new_text = "\n%s" % part[len_last_content:]
                                elif role == "tool" and last_role != "tool":
                                    new_text = ">%s\n%s" % (initial_text, part[len_last_content:])
                                else:
                                    new_text = part[len_last_content:]
                            len_last_content = len(part)
                elif outer_content_type == "multimodal_text":
                    new_text = "[multimodal content]"
                else:
                    text = content.get("text", "")
                    if outer_content_type == "code" and last_content_type != "code":
                        language = content.get("language", "") or "unknown"
                        new_text = "\n```%s\n%s" % (language, text[len_last_content:])
                    elif (
                        outer_content_type == "execution_output"
                        and last_content_type != "execution_output"
                    ):
                        new_text = "\n```Output\n%s" % text[len_last_content:]
                    else:
                        new_text = text[len_last_content:]
                    len_last_content = len(text)

                if last_content_type == "code" and outer_content_type != "code":
                    new_text = "\n```\n%s" % new_text
                elif (
                    last_content_type == "execution_output"
                    and outer_content_type != "execution_output"
                ):
                    new_text = "\n```\n%s" % new_text

                delta = {"content": new_text}
                last_content_type = outer_content_type
                if completion_tokens >= max_tokens:
                    delta = {}
                    finish_reason = "length"
                    end = True

            elif status == "finished_successfully":
                if message.get("end_turn"):
                    part = content.get("parts", [])[0]
                    new_text = part[len_last_content:] if part else ""
                    if new_text:
                        delta = {"content": new_text}
                    else:
                        delta = {}
                    finish_reason = "stop"
                    end = True
                else:
                    len_last_content = 0
                    if meta_data.get("finished_text"):
                        delta = {"content": "\n%s\n" % meta_data.get("finished_text")}
                    else:
                        continue
            else:
                continue

            last_message_id = message_id
            last_role = role

            if not end and not delta.get("content"):
                delta = {"role": "assistant", "content": ""}

            chunk_new = {
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
            completion_tokens += 1
            yield "data: %s\n\n" % json.dumps(chunk_new)

        except Exception as e:
            if chunk.startswith("data: "):
                try:
                    err_data = json.loads(chunk[6:])
                    if err_data.get("error"):
                        logger.error("Error: %s" % err_data.get("error"))
                        yield "data: [DONE]\n\n"
                        break
                except json.JSONDecodeError:
                    pass
            logger.error("Stream parse error: %s" % e)
            continue


async def format_not_stream_response(
    response: AsyncGenerator,
    prompt_tokens: int,
    max_tokens: int,
    model: str,
) -> dict:
    chat_id = _generate_chat_id()
    created_time = int(time.time())
    all_text = ""

    async for chunk in response:
        try:
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8")
            if chunk.startswith("data: [DONE]"):
                break
            elif not chunk.startswith("data: "):
                continue
            chunk_data = json.loads(chunk[6:])
            delta = chunk_data.get("choices", [{}])[0].get("delta")
            if not delta:
                continue
            all_text += delta.get("content", "")
        except Exception as e:
            logger.error("Non-stream collect error: %s" % e)
            continue

    content, completion_tokens, finish_reason = _split_tokens_from_content(all_text, max_tokens)
    message = {"role": "assistant", "content": content}
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if not content:
        raise HTTPException(status_code=403, detail="No content in the message.")

    data = {
        "id": chat_id,
        "object": "chat.completion",
        "created": created_time,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }
    return data


def _split_tokens_from_content(text: str, max_tokens: int) -> Tuple[str, int, str]:
    approx_tokens = len(text) // 4
    if approx_tokens > max_tokens:
        cutoff = max_tokens * 4
        return text[:cutoff], max_tokens, "length"
    return text, approx_tokens, "stop"

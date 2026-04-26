from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from providers import get_provider
from providers.tools.parser import extract_tool_call
from providers.tools.prompt import inject_tool_system_prompt
from providers.tools.registry import run_tool

logger = logging.getLogger(__name__)

try:
    _MAX_ITERS = int(os.getenv("TOOL_LOOP_MAX_ITERS", "5"))
except ValueError:
    _MAX_ITERS = 5


def _format_tool_result(name: str, result: Any) -> str:
    payload = json.dumps(result, ensure_ascii=False, default=str)
    return "```tool_result\nname: %s\n%s\n```" % (name, payload)


def _extract_assistant_text(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    return content if isinstance(content, str) else ""


async def _run_one_turn(
    provider_name: str,
    request_data: Dict[str, Any],
    req_token: Optional[str],
    profile: Optional[str],
) -> str:
    entry = get_provider(provider_name)
    service = entry.service(req_token)
    try:
        await service.set_dynamic_data(request_data, profile=profile)
        await service.get_chat_requirements()
        await service.prepare_send_conversation()
        response = await service.send_conversation()
        return _extract_assistant_text(response)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Server error: %s" % exc)
    finally:
        await service.close_client()


async def run_loop(
    provider_name: str,
    request_data: Dict[str, Any],
    req_token: Optional[str],
    profile: Optional[str],
) -> Tuple[str, str, List[Dict[str, Any]], int]:
    messages = inject_tool_system_prompt(list(request_data.get("messages", [])))
    final_text = ""
    last_text = ""
    iterations = 0

    for iterations in range(1, _MAX_ITERS + 1):
        sub_request = {**request_data, "messages": messages, "stream": False}
        sub_request.pop("tools", None)
        sub_request.pop("tool_choice", None)
        sub_request.pop("enable_tools", None)

        text = await _run_one_turn(provider_name, sub_request, req_token, profile)
        last_text = text

        call = extract_tool_call(text)
        if call is None:
            final_text = text
            break

        messages.append({"role": "assistant", "content": text})

        if call.name == "__invalid__":
            err = call.arguments.get("_error", "invalid tool call")
            messages.append(
                {
                    "role": "user",
                    "content": _format_tool_result("error", {"error": err}),
                }
            )
            continue

        result = await run_tool(call.name, call.arguments)
        logger.info("tool_loop: ran %s (iter %d)", call.name, iterations)
        messages.append(
            {"role": "user", "content": _format_tool_result(call.name, result)}
        )
    else:
        final_text = (
            last_text
            or "(tool loop reached the iteration cap without producing a final answer.)"
        )

    return final_text, request_data.get("model", ""), messages, iterations


def _build_completion_dict(text: str, model: str) -> Dict[str, Any]:
    return {
        "id": "chatcmpl-tool-%s" % uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _build_stream_generator(text: str, model: str):
    completion_id = "chatcmpl-tool-%s" % uuid.uuid4().hex[:24]
    created = int(time.time())

    async def generator():
        first = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
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
        yield "data: %s\n\n" % json.dumps(first)

        body = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": text},
                    "logprobs": None,
                    "finish_reason": None,
                }
            ],
        }
        yield "data: %s\n\n" % json.dumps(body)

        final = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
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
        yield "data: %s\n\n" % json.dumps(final)
        yield "data: [DONE]\n\n"

    return generator()


async def run_with_tools_route(
    provider_name: str,
    request_data: Dict[str, Any],
    req_token: Optional[str],
    profile: Optional[str],
):
    final_text, model, _messages, _iters = await run_loop(
        provider_name, request_data, req_token, profile
    )

    if request_data.get("stream", False):
        return StreamingResponse(
            _build_stream_generator(final_text, model),
            media_type="text/event-stream",
        )
    return JSONResponse(
        _build_completion_dict(final_text, model),
        media_type="application/json",
    )

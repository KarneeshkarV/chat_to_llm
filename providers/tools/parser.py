from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional


_FENCE_RE = re.compile(r"```tool_call\s*\n(.*?)\n```", re.DOTALL)


@dataclass
class ToolCall:
    name: str
    arguments: Dict[str, Any]
    raw: str


def extract_tool_call(text: str) -> Optional[ToolCall]:
    if not isinstance(text, str):
        return None
    match = _FENCE_RE.search(text)
    if not match:
        return None
    body = match.group(1).strip()
    raw = match.group(0)

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        return ToolCall(
            name="__invalid__",
            arguments={"_raw": body, "_error": "JSON decode failed: %s" % exc},
            raw=raw,
        )

    if not isinstance(parsed, dict):
        return ToolCall(
            name="__invalid__",
            arguments={"_raw": body, "_error": "tool_call body must be a JSON object"},
            raw=raw,
        )

    name = parsed.get("name")
    arguments = parsed.get("arguments", {})

    if not isinstance(name, str) or not name:
        return ToolCall(
            name="__invalid__",
            arguments={"_raw": body, "_error": "missing or non-string 'name'"},
            raw=raw,
        )
    if not isinstance(arguments, dict):
        return ToolCall(
            name="__invalid__",
            arguments={"_raw": body, "_error": "'arguments' must be a JSON object"},
            raw=raw,
        )
    return ToolCall(name=name, arguments=arguments, raw=raw)


def strip_tool_call(text: str, call: ToolCall) -> str:
    return text.replace(call.raw, "", 1).strip()

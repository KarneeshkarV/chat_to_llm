from __future__ import annotations

from typing import Any, Dict, List

from providers.tools.registry import list_tool_specs


_TEMPLATE = """You have access to the following tools. When you need to call one, respond
with EXACTLY ONE fenced code block tagged `tool_call` and nothing else after it:

```tool_call
{{"name": "<tool_name>", "arguments": {{...}}}}
```

The next user message will contain the result inside a `tool_result` block.
Continue reasoning, call another tool if needed, or write the final answer in
plain text. Do not call a tool when you can already answer.

Available tools:
{tools}"""


def system_prompt_fragment() -> str:
    lines = [
        "- %s(%s): %s" % (spec.name, spec.signature, spec.description)
        for spec in list_tool_specs()
    ]
    return _TEMPLATE.format(tools="\n".join(lines))


def inject_tool_system_prompt(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fragment = system_prompt_fragment()
    out = list(messages)

    if out and out[0].get("role") == "system":
        first = out[0]
        original = first.get("content", "")
        if isinstance(original, str):
            out[0] = {**first, "content": (original + "\n\n" + fragment).strip()}
            return out
        if isinstance(original, list):
            new_content = list(original) + [{"type": "text", "text": "\n\n" + fragment}]
            out[0] = {**first, "content": new_content}
            return out

    out.insert(0, {"role": "system", "content": fragment})
    return out

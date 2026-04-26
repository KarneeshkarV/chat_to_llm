from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict

from providers.tools.web_search import web_search


@dataclass(frozen=True)
class ToolSpec:
    name: str
    signature: str
    description: str
    runner: Callable[..., Awaitable[Any]]


_REGISTRY: Dict[str, ToolSpec] = {
    "web_search": ToolSpec(
        name="web_search",
        signature="query: str",
        description=(
            "Search the web via DuckDuckGo. Returns up to 5 results "
            "with title, url, and snippet."
        ),
        runner=web_search,
    ),
}


def list_tool_specs() -> list[ToolSpec]:
    return list(_REGISTRY.values())


def is_registered(name: str) -> bool:
    return name in _REGISTRY


async def run_tool(name: str, arguments: Dict[str, Any]) -> Any:
    spec = _REGISTRY.get(name)
    if spec is None:
        return {"error": "unknown tool: %s" % name}
    if not isinstance(arguments, dict):
        return {"error": "arguments must be an object"}
    try:
        return await spec.runner(**arguments)
    except TypeError as exc:
        return {"error": "bad arguments for %s: %s" % (name, exc)}
    except Exception as exc:
        return {"error": "%s raised: %s" % (name, exc)}

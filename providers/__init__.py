from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from providers.base import BaseAuth, BaseFormatter, BaseService
from providers.chatgpt.auth import ChatGPTAuth as _ChatGPTAuth
from providers.chatgpt.formatting import (
    format_not_stream_response,
    stream_response,
)
from providers.chatgpt.service import ChatGPTService as _ChatGPTService
from providers.claude.auth import ClaudeAuth as _ClaudeAuth
from providers.claude.formatting import ClaudeFormatter as _ClaudeFormatter
from providers.claude.service import ClaudeService as _ClaudeService
from providers.gemini.auth import GeminiAuth as _GeminiAuth
from providers.gemini.formatting import GeminiFormatter as _GeminiFormatter
from providers.gemini.service import GeminiService as _GeminiService
from providers.grok.auth import GrokAuth as _GrokAuth
from providers.grok.formatting import GrokFormatter as _GrokFormatter
from providers.grok.service import GrokService as _GrokService


@dataclass
class ProviderEntry:
    auth: type[BaseAuth]
    service: type[BaseService]
    formatter: type[BaseFormatter]
    name: str
    matches: Callable[[str], bool]


class ChatGPTFormatter(BaseFormatter):
    async def stream_response(self, service, response, model, max_tokens):
        async for chunk in stream_response(service, response, model, max_tokens):
            yield chunk

    async def format_not_stream_response(self, response, prompt_tokens, max_tokens, model):
        return await format_not_stream_response(response, prompt_tokens, max_tokens, model)


def _matches_chatgpt(model: str) -> bool:
    name = (model or "").lower()
    if not name:
        return False
    return (
        name.startswith("gpt-")
        or name.startswith("gpt")
        or name.startswith("o1")
        or name.startswith("o3")
        or name.startswith("o4")
        or name.startswith("text-davinci")
        or name.startswith("chatgpt")
        or name == "auto"
    )


def _matches_claude(model: str) -> bool:
    return (model or "").lower().startswith("claude")


def _matches_gemini(model: str) -> bool:
    return (model or "").lower().startswith("gemini")


def _matches_grok(model: str) -> bool:
    return (model or "").lower().startswith("grok")


_REGISTRY: dict[str, ProviderEntry] = {
    "chatgpt": ProviderEntry(
        auth=_ChatGPTAuth,
        service=_ChatGPTService,
        formatter=ChatGPTFormatter,
        name="chatgpt",
        matches=_matches_chatgpt,
    ),
    "claude": ProviderEntry(
        auth=_ClaudeAuth,
        service=_ClaudeService,
        formatter=_ClaudeFormatter,
        name="claude",
        matches=_matches_claude,
    ),
    "gemini": ProviderEntry(
        auth=_GeminiAuth,
        service=_GeminiService,
        formatter=_GeminiFormatter,
        name="gemini",
        matches=_matches_gemini,
    ),
    "grok": ProviderEntry(
        auth=_GrokAuth,
        service=_GrokService,
        formatter=_GrokFormatter,
        name="grok",
        matches=_matches_grok,
    ),
}


def register_provider(name: str, entry: ProviderEntry) -> None:
    _REGISTRY[name] = entry


def get_provider(name: str) -> ProviderEntry:
    if name not in _REGISTRY:
        raise KeyError("Unknown provider: %s" % name)
    return _REGISTRY[name]


def list_providers() -> list[str]:
    return list(_REGISTRY.keys())


def resolve_provider_for_model(model: str) -> Optional[ProviderEntry]:
    for entry in _REGISTRY.values():
        try:
            if entry.matches(model):
                return entry
        except Exception:
            continue
    return None


__all__ = [
    "ProviderEntry",
    "get_provider",
    "list_providers",
    "register_provider",
    "resolve_provider_for_model",
    "ChatGPTFormatter",
]

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from providers.base import BaseAuth, BaseFormatter, BaseService

if TYPE_CHECKING:
    from providers.chatgpt.auth import ChatGPTAuth
    from providers.chatgpt.service import ChatGPTService


@dataclass
class ProviderEntry:
    auth: type[BaseAuth]
    service: type[BaseService]
    formatter: type[BaseFormatter]
    name: str


# Import here to avoid circular imports at module level
from providers.chatgpt.auth import ChatGPTAuth as _ChatGPTAuth
from providers.chatgpt.service import ChatGPTService as _ChatGPTService
from providers.chatgpt.formatting import (
    stream_response,
    format_not_stream_response,
)
from providers.claude.auth import ClaudeAuth as _ClaudeAuth
from providers.claude.service import ClaudeService as _ClaudeService
from providers.claude.formatting import ClaudeFormatter as _ClaudeFormatter
from providers.grok.auth import GrokAuth as _GrokAuth
from providers.grok.service import GrokService as _GrokService
from providers.grok.formatting import GrokFormatter as _GrokFormatter


class ChatGPTFormatter(BaseFormatter):
    async def stream_response(self, service, response, model, max_tokens):
        async for chunk in stream_response(service, response, model, max_tokens):
            yield chunk

    async def format_not_stream_response(self, response, prompt_tokens, max_tokens, model):
        return await format_not_stream_response(response, prompt_tokens, max_tokens, model)


_REGISTRY: dict[str, ProviderEntry] = {
    "chatgpt": ProviderEntry(
        auth=_ChatGPTAuth,
        service=_ChatGPTService,
        formatter=ChatGPTFormatter,
        name="chatgpt",
    ),
    "claude": ProviderEntry(
        auth=_ClaudeAuth,
        service=_ClaudeService,
        formatter=_ClaudeFormatter,
        name="claude",
    ),
    "grok": ProviderEntry(
        auth=_GrokAuth,
        service=_GrokService,
        formatter=_GrokFormatter,
        name="grok",
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


__all__ = [
    "ProviderEntry",
    "get_provider",
    "list_providers",
    "register_provider",
    "ChatGPTFormatter",
]

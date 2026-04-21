from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import HTTPException

from providers.base import BaseService
from providers.claude.auth import ClaudeAuth

logger = logging.getLogger(__name__)


class ClaudeService(BaseService):
    def __init__(self, token: Optional[str] = None) -> None:
        super().__init__(token)
        self._auth = ClaudeAuth()
        self.access_token: Optional[str] = None
        self.data: Dict[str, Any] = {}
        self.origin_model = ""
        self.resp_model = ""
        self.max_tokens = 4096

    async def set_dynamic_data(self, data: Dict[str, Any], profile: Optional[str] = None) -> None:
        self.data = data
        self.origin_model = data.get("model", "claude-3-5-sonnet")
        self.resp_model = self.origin_model
        self.max_tokens = data.get("max_tokens", 4096)
        if self.req_token and self._auth.is_browser_token(self.req_token):
            self.access_token = await self._auth.get_access_token(profile=profile)
        else:
            self.access_token = self.req_token

    async def get_chat_requirements(self) -> Optional[str]:
        # TODO: implement Claude-specific pre-flight handshake
        return None

    async def prepare_send_conversation(self) -> dict:
        # TODO: build Claude-native payload
        return {}

    async def send_conversation(self) -> Any:
        raise HTTPException(status_code=501, detail="Claude chat service not yet implemented.")

    async def close_client(self) -> None:
        pass

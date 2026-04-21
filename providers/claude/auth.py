from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

from providers.base import BaseAuth

logger = logging.getLogger(__name__)

BROWSER_TOKEN_ALIASES = {"claude-browser", "claude-cookies"}

_CLAUDE_BROWSER_AUTH = False  # TODO: read from env when implemented


class ClaudeAuth(BaseAuth):
    def is_browser_token(self, token: Optional[str]) -> bool:
        if not token:
            return False
        lower = token.strip().lower()
        if lower in BROWSER_TOKEN_ALIASES:
            return True
        if ":" in lower:
            prefix = lower.split(":", 1)[0]
            return prefix in BROWSER_TOKEN_ALIASES
        return False

    def parse_browser_token(self, token: Optional[str]) -> Tuple[bool, Optional[str]]:
        if not token:
            return False, None
        lower = token.strip().lower()
        if lower in BROWSER_TOKEN_ALIASES:
            return True, None
        if ":" in token:
            prefix, profile = token.split(":", 1)
            if prefix.strip().lower() in BROWSER_TOKEN_ALIASES:
                return True, profile.strip() or None
        return False, None

    def ensure_browser_auth_request_allowed(self, request: Any = None) -> None:
        if not _CLAUDE_BROWSER_AUTH:
            raise HTTPException(
                status_code=403,
                detail="Claude browser cookie auth is not yet implemented.",
            )

    async def get_access_token(self, profile: Optional[str] = None) -> str:
        raise HTTPException(status_code=501, detail="Claude browser auth not yet implemented.")

    async def get_all_sessions(self, force_refresh: bool = False) -> Dict[str, Any]:
        raise HTTPException(status_code=501, detail="Claude browser auth not yet implemented.")

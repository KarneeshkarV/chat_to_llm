from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator, Dict, Optional


class BaseAuth(ABC):
    """Browser cookie extraction + session token exchange."""

    @abstractmethod
    def is_browser_token(self, token: str) -> bool:
        """Return True if the given token should trigger browser cookie auth."""
        ...

    @abstractmethod
    def parse_browser_token(self, token: str) -> tuple[bool, Optional[str]]:
        """Parse a browser token and return (is_browser, profile)."""
        ...

    @abstractmethod
    async def get_access_token(self, profile: Optional[str] = None) -> str:
        """Get an access token from browser cookies."""
        ...

    @abstractmethod
    async def get_all_sessions(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Get all browser sessions across profiles."""
        ...

    @abstractmethod
    async def get_browser_session(
        self, force_refresh: bool = False, profile: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get a single browser-backed session preview."""
        ...

    @abstractmethod
    def ensure_browser_auth_request_allowed(self, request: Any) -> None:
        """Raise HTTPException if browser auth is not allowed for this request."""
        ...


class BaseService(ABC):
    """Per-request conversation handler."""

    def __init__(self, token: Optional[str] = None) -> None:
        self.req_token = token

    @abstractmethod
    async def set_dynamic_data(self, data: Dict[str, Any], profile: Optional[str] = None) -> None:
        """Prepare request-specific data (model, messages, headers, etc.)."""
        ...

    @abstractmethod
    async def get_chat_requirements(self) -> Optional[str]:
        """Perform any pre-flight auth/requirements handshake."""
        ...

    @abstractmethod
    async def prepare_send_conversation(self) -> dict:
        """Build the final payload to send to the provider backend."""
        ...

    @abstractmethod
    async def send_conversation(self) -> Any:
        """Send the conversation and return a response or async generator."""
        ...

    @abstractmethod
    async def close_client(self) -> None:
        """Clean up any HTTP clients."""
        ...


class BaseFormatter(ABC):
    """Stream / response formatting to OpenAI-compatible format."""

    @abstractmethod
    async def stream_response(
        self, service: Any, response: Any, model: str, max_tokens: int
    ) -> AsyncGenerator[str, None]:
        """Convert provider SSE stream to OpenAI-compatible SSE chunks."""
        ...

    @abstractmethod
    async def format_not_stream_response(
        self,
        response: AsyncGenerator,
        prompt_tokens: int,
        max_tokens: int,
        model: str,
    ) -> dict:
        """Collect a streaming response into a single non-streaming OpenAI response."""
        ...

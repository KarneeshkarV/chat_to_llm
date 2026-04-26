from __future__ import annotations

import asyncio
import os

import pytest
from fastapi import HTTPException

from providers.gemini.service import GeminiService


_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class TestGeminiService:
    def test_merge_messages_text_only(self):
        svc = GeminiService()
        prompt, files = svc._merge_messages(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "How are you?"},
            ]
        )
        assert "System:" in prompt
        assert "You are helpful" in prompt
        assert "User: Hello" in prompt
        assert "Assistant: Hi" in prompt
        assert "User: How are you?" in prompt
        assert files == []

    def test_parse_message_content_with_data_url_image(self):
        svc = GeminiService()
        text, files = svc._parse_message_content(
            [
                {"type": "text", "text": "Describe this image"},
                {"type": "image_url", "image_url": {"url": _PNG_DATA_URL}},
            ]
        )
        assert text == "Describe this image"
        assert len(files) == 1
        assert os.path.exists(files[0])
        asyncio.run(svc.close_client())

    def test_validate_request_silently_accepts_tools(self):
        svc = GeminiService()
        svc.data = {
            "messages": [{"role": "user", "content": "Hi"}],
            "tools": [{"type": "function"}],
            "tool_choice": "auto",
        }
        svc._validate_request()

    def test_parse_message_content_rejects_remote_images(self):
        svc = GeminiService()
        with pytest.raises(HTTPException) as exc_info:
            svc._parse_message_content(
                [{"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}]
            )
        assert exc_info.value.status_code == 400
        assert "local file paths or base64 data urls" in exc_info.value.detail.lower()

    def test_parse_credentials_from_raw_cookie_header(self):
        svc = GeminiService()
        secure_1psid, secure_1psidts = svc._parse_credentials_from_token(
            "__Secure-1PSID=abc; __Secure-1PSIDTS=def"
        )
        assert secure_1psid == "abc"
        assert secure_1psidts == "def"

    def test_prepare_send_conversation(self):
        svc = GeminiService()
        svc.data = {
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        }
        svc.resp_model = "gemini-2.5-flash"
        svc.max_tokens = 1024

        payload = asyncio.run(svc.prepare_send_conversation())
        assert payload["model"] == "gemini-2.5-flash"
        assert payload["prompt"] == "User: Hello"
        assert payload["files"] == []

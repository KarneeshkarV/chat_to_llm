from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from providers.claude.service import ClaudeService


_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class TestClaudeService:
    def test_merge_messages_text_only(self):
        svc = ClaudeService()
        merged, images = svc._merge_messages(
            [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
                {"role": "user", "content": "How are you?"},
            ]
        )
        assert "You are helpful" in merged
        assert "Human: Hello" in merged
        assert "Assistant: Hi" in merged
        assert "Human: How are you?" in merged
        assert images == []

    def test_parse_message_content_with_data_url_image(self):
        svc = ClaudeService()
        text, images = svc._parse_message_content(
            [
                {"type": "text", "text": "Describe this image"},
                {"type": "image_url", "image_url": {"url": _PNG_DATA_URL}},
            ]
        )
        assert text == "Describe this image"
        assert len(images) == 1
        assert images[0].media_type == "image/png"

    def test_validate_request_rejects_tools(self):
        svc = ClaudeService()
        svc.data = {
            "messages": [{"role": "user", "content": "Hi"}],
            "tools": [{"type": "function"}],
        }
        with pytest.raises(HTTPException) as exc_info:
            svc._validate_request()
        assert exc_info.value.status_code == 400
        assert "tool calling" in exc_info.value.detail.lower()

    def test_parse_message_content_rejects_non_data_url_images(self):
        svc = ClaudeService()
        with pytest.raises(HTTPException) as exc_info:
            svc._parse_message_content(
                [{"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}]
            )
        assert exc_info.value.status_code == 400
        assert "data url" in exc_info.value.detail.lower()

    def test_build_payload(self, monkeypatch):
        svc = ClaudeService()
        svc.data = {
            "model": "claude-3-5-sonnet-latest",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        }
        svc.resp_model = "claude-3-5-sonnet-latest"
        svc.max_tokens = 1024
        svc._org_uuid = "org-123"

        async def fake_upload(_image):
            return "file-123"

        monkeypatch.setattr(svc, "_upload_image", fake_upload)

        payload = asyncio.run(svc._build_payload())
        assert payload["model"] == "claude-3-5-sonnet-latest"
        assert payload["attachments"][0]["file_name"] == "paste.txt"
        assert payload["prompt"] == ""
        assert payload["rendering_mode"] == "raw"
        assert payload["files"] == []

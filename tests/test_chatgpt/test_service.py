from __future__ import annotations

import asyncio
import base64

import pytest
from fastapi import HTTPException

from providers.chatgpt.files import (
    determine_file_use_case,
    get_file_content_from_data_url,
    get_file_extension,
    get_image_size,
)
from providers.chatgpt.formatting import api_messages_to_chat


_PIXEL_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class _StubService:
    def __init__(self) -> None:
        self.upload_calls = 0

    async def upload_file_from_data_url(self, url: str) -> dict:
        self.upload_calls += 1
        return {
            "file_id": "file-test123",
            "file_name": "image-test.png",
            "size_bytes": 42,
            "mime_type": "image/png",
            "width": 1,
            "height": 1,
            "use_case": "multimodal",
        }


class TestDataURLDecode:
    def test_decodes_valid_data_url(self):
        url = "data:image/png;base64,%s" % _PIXEL_PNG_B64
        raw, mime = get_file_content_from_data_url(url)
        assert mime == "image/png"
        assert raw.startswith(b"\x89PNG")

    def test_rejects_plain_https(self):
        with pytest.raises(HTTPException) as exc:
            get_file_content_from_data_url("https://example.com/cat.png")
        assert exc.value.status_code == 400


class TestImageSize:
    def test_reads_1x1_png(self):
        raw = base64.b64decode(_PIXEL_PNG_B64)
        w, h = get_image_size(raw)
        assert w == 1
        assert h == 1


class TestUseCaseMapping:
    def test_image_is_multimodal(self):
        assert determine_file_use_case("image/png") == "multimodal"
        assert determine_file_use_case("image/jpeg") == "multimodal"

    def test_pdf_is_my_files(self):
        assert determine_file_use_case("application/pdf") == "my_files"

    def test_other_is_ace_upload(self):
        assert determine_file_use_case("text/csv") == "ace_upload"


class TestExtensionMapping:
    def test_known_mimes(self):
        assert get_file_extension("image/png") == ".png"
        assert get_file_extension("image/jpeg") == ".jpg"
        assert get_file_extension("application/pdf") == ".pdf"

    def test_unknown_mime_defaults(self):
        assert get_file_extension("application/x-whatever") == ".bin"


class TestApiMessagesToChat:
    def test_multimodal_emits_asset_pointer(self):
        svc = _StubService()
        api_messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What color?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,%s" % _PIXEL_PNG_B64},
                    },
                ],
            }
        ]
        chat = asyncio.run(api_messages_to_chat(svc, api_messages))
        assert len(chat) == 1
        msg = chat[0]
        assert msg["content"]["content_type"] == "multimodal_text"
        parts = msg["content"]["parts"]
        assert parts[0] == "What color?"
        assert parts[1]["content_type"] == "image_asset_pointer"
        assert parts[1]["asset_pointer"] == "file-service://file-test123"
        assert parts[1]["width"] == 1
        assert msg["metadata"]["attachments"][0]["id"] == "file-test123"
        assert svc.upload_calls == 1

    def test_text_only_stays_text(self):
        svc = _StubService()
        chat = asyncio.run(
            api_messages_to_chat(svc, [{"role": "user", "content": "Hello"}])
        )
        assert chat[0]["content"]["content_type"] == "text"
        assert chat[0]["content"]["parts"] == ["Hello"]
        assert svc.upload_calls == 0

    def test_list_without_image_stays_text(self):
        svc = _StubService()
        chat = asyncio.run(
            api_messages_to_chat(
                svc,
                [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": "Only text here"}],
                    }
                ],
            )
        )
        assert chat[0]["content"]["content_type"] == "text"
        assert chat[0]["content"]["parts"] == ["Only text here"]
        assert svc.upload_calls == 0

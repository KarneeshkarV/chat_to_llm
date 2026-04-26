from __future__ import annotations

import base64
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from providers.base import BaseService
from providers.claude.auth import ClaudeAuth
from providers.claude.formatting import ClaudeFormatter
from providers.common import Client

logger = logging.getLogger(__name__)

_HOST_URL = os.getenv("CLAUDE_BASE_URL", "https://claude.ai").rstrip("/")
_PROXY_URL = os.getenv("PROXY_URL", "").strip() or None

_IMAGE_MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


@dataclass
class _ImageAttachment:
    data: bytes
    media_type: str
    filename: str


def _estimate_tokens_from_text(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _timezone_name() -> str:
    return datetime.now().astimezone().tzname() or "UTC"


def _message_prefix(role: str) -> str:
    if role == "assistant":
        return "Assistant"
    return "Human"


class ClaudeService(BaseService):
    def __init__(self, token: Optional[str] = None) -> None:
        super().__init__(token)
        self._auth = ClaudeAuth()
        self._formatter = ClaudeFormatter()
        self._client: Optional[Client] = None
        self._profile: Optional[str] = None

        self.cookie_header: Optional[str] = None
        self.data: Dict[str, Any] = {}
        self.origin_model = ""
        self.resp_model = ""
        self.max_tokens = 4096
        self.prompt_tokens = 0

        self._user: Optional[Dict[str, Any]] = None
        self._org_uuid: Optional[str] = None
        self._conversation_id: Optional[str] = None
        self._prepared_payload: Optional[Dict[str, Any]] = None

    async def set_dynamic_data(self, data: Dict[str, Any], profile: Optional[str] = None) -> None:
        self.data = data
        self._profile = profile
        self.origin_model = data.get("model", "claude-3-5-sonnet-latest")
        self.resp_model = self.origin_model
        self.max_tokens = data.get("max_tokens", 4096)
        if not isinstance(self.max_tokens, int):
            self.max_tokens = 4096

        if self.req_token and self._auth.is_browser_token(self.req_token):
            session = await self._auth.get_browser_session(profile=profile)
            self.cookie_header = session["cookie_header"]
            self._user = session.get("user")
            self._org_uuid = session.get("org_uuid")
        else:
            self.cookie_header = self.req_token

        self.prompt_tokens = self._estimate_prompt_tokens(self.data.get("messages", []))
        self._client = Client(proxy=_PROXY_URL, impersonate="chrome120")

    async def get_chat_requirements(self) -> Optional[str]:
        if not self.cookie_header:
            raise HTTPException(status_code=401, detail="No Claude cookies available.")

        if not self._org_uuid:
            session = await self._auth.validate_cookie_header(
                self.cookie_header,
                profile_key=self._profile or "manual",
            )
            self._user = session.get("user")
            self._org_uuid = session.get("org_uuid")
        if not self._org_uuid:
            raise HTTPException(status_code=401, detail="No Claude organization with chat access.")
        return None

    async def prepare_send_conversation(self) -> dict:
        self._validate_request()
        payload = await self._build_payload()
        self._prepared_payload = payload
        return payload

    async def send_conversation(self) -> Any:
        if not self._prepared_payload:
            await self.prepare_send_conversation()

        conversation_id = await self._create_conversation()
        self._conversation_id = conversation_id
        await self._set_conversation_settings()

        completion_response = await self._client.post(
            self._conversation_completion_url(),
            headers=self._headers(accept="text/event-stream"),
            json=self._prepared_payload,
            timeout=120,
            stream=True,
        )

        if completion_response.status_code in (401, 403):
            raise HTTPException(
                status_code=401, detail="Claude browser cookies are expired or invalid."
            )
        if completion_response.status_code == 429:
            raise HTTPException(status_code=429, detail="rate-limit")
        if completion_response.status_code != 200:
            detail = await self._response_detail(completion_response)
            raise HTTPException(status_code=completion_response.status_code, detail=detail)

        content_type = completion_response.headers.get("Content-Type", "")
        if "text/event-stream" not in content_type:
            detail = await self._response_detail(completion_response)
            raise HTTPException(
                status_code=502,
                detail="Claude completion response was not SSE: %s" % detail,
            )

        line_stream = completion_response.aiter_lines()
        if self.data.get("stream", False):
            return self._formatter.stream_response(
                self,
                line_stream,
                self.resp_model,
                self.max_tokens,
            )

        return await self._formatter.format_not_stream_response(
            self._formatter.stream_response(
                self,
                line_stream,
                self.resp_model,
                self.max_tokens,
            ),
            self.prompt_tokens,
            self.max_tokens,
            self.resp_model,
        )

    async def close_client(self) -> None:
        if self._conversation_id and self._client:
            try:
                await self._client.request(
                    "DELETE",
                    self._conversation_url(),
                    headers=self._headers(accept="application/json"),
                    timeout=20,
                )
            except Exception as exc:
                logger.warning("Claude conversation cleanup failed: %s", exc)
            finally:
                self._conversation_id = None

        if self._client:
            await self._client.close()
            self._client = None

    def _validate_request(self) -> None:
        if self.data.get("n") not in (None, 1):
            raise HTTPException(
                status_code=400,
                detail="Claude web only supports a single completion on this endpoint.",
            )

        if not self.data.get("messages"):
            raise HTTPException(status_code=400, detail="Claude request must include messages.")

    async def _build_payload(self) -> Dict[str, Any]:
        merged_text, images = self._merge_messages(self.data.get("messages", []))
        attachments = [
            {
                "extracted_content": merged_text,
                "file_name": "paste.txt",
                "file_type": "txt",
                "file_size": len(merged_text.encode("utf-8")),
            }
        ]
        uploaded_files = []
        for image in images:
            uploaded_files.append(await self._upload_image(image))

        return {
            "max_tokens_to_sample": self.max_tokens,
            "attachments": attachments,
            "files": uploaded_files,
            "model": self.resp_model,
            "rendering_mode": "messages" if self.data.get("stream", False) else "raw",
            "prompt": "",
            "timezone": _timezone_name(),
            "tools": [],
        }

    def _merge_messages(self, messages: List[dict]) -> Tuple[str, List[_ImageAttachment]]:
        system_parts: List[str] = []
        dialogue_parts: List[str] = []
        images: List[_ImageAttachment] = []

        for message in messages:
            role = message.get("role", "user")
            if role not in {"system", "user", "assistant"}:
                raise HTTPException(status_code=400, detail="Unsupported Claude role: %s" % role)

            text, message_images = self._parse_message_content(message.get("content", ""))
            images.extend(message_images)
            if role == "system":
                if text:
                    system_parts.append(text)
                continue
            if text:
                dialogue_parts.append("%s: %s" % (_message_prefix(role), text))

        merged_parts = []
        if system_parts:
            merged_parts.append("\n".join(part for part in system_parts if part))
        if dialogue_parts:
            merged_parts.append("\n\n".join(dialogue_parts))

        merged = "\n\n".join(part for part in merged_parts if part).strip()
        if not merged:
            raise HTTPException(status_code=400, detail="Claude request produced an empty prompt.")
        return merged, images

    def _parse_message_content(self, content: Any) -> Tuple[str, List[_ImageAttachment]]:
        if isinstance(content, str):
            return content.strip(), []
        if not isinstance(content, list):
            raise HTTPException(
                status_code=400, detail="Claude message content must be text or content parts."
            )

        text_parts: List[str] = []
        images: List[_ImageAttachment] = []
        for item in content:
            item_type = item.get("type")
            if item_type == "text":
                text_parts.append(str(item.get("text", "")).strip())
            elif item_type == "image_url":
                image_url = item.get("image_url", {})
                url = image_url.get("url", "")
                images.append(self._decode_image_url(url))
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Unsupported Claude content part: %s" % item_type,
                )

        return "\n".join(part for part in text_parts if part).strip(), images

    def _decode_image_url(self, url: str) -> _ImageAttachment:
        if not isinstance(url, str) or not url:
            raise HTTPException(
                status_code=400, detail="Claude image_url must be a non-empty string."
            )
        if not url.startswith("data:"):
            raise HTTPException(
                status_code=400,
                detail="Claude web currently only supports data URL images on this endpoint.",
            )

        try:
            header, b64_data = url.split(",", 1)
            mime = header.split(";")[0][5:]
            raw = base64.b64decode(b64_data)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid Claude data URL image: %s" % exc)

        extension = _IMAGE_MIME_TO_EXT.get(mime)
        if not extension:
            raise HTTPException(
                status_code=400, detail="Unsupported Claude image media type: %s" % mime
            )
        return _ImageAttachment(data=raw, media_type=mime, filename="upload.%s" % extension)

    async def _upload_image(self, image: _ImageAttachment) -> str:
        from curl_cffi import CurlMime

        mp = CurlMime()
        mp.addpart(
            name="file",
            filename=image.filename,
            content_type=image.media_type,
            data=image.data,
        )
        response = await self._client.post(
            f"{_HOST_URL}/api/{self._org_uuid}/upload",
            headers=self._headers(accept="application/json", include_content_type=False),
            multipart=mp,
            timeout=60,
        )
        if response.status_code != 200:
            detail = await self._response_detail(response)
            raise HTTPException(
                status_code=response.status_code, detail="Claude image upload failed: %s" % detail
            )

        try:
            payload = response.json()
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail="Claude image upload response was not JSON: %s" % exc
            )

        file_uuid = payload.get("file_uuid")
        if not file_uuid:
            raise HTTPException(
                status_code=502, detail="Claude image upload did not return file_uuid."
            )
        return file_uuid

    async def _create_conversation(self) -> str:
        conversation_id = str(uuid.uuid4())
        body = {
            "uuid": conversation_id,
            "name": "chat-to-llm-%s" % datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }
        response = await self._client.post(
            f"{_HOST_URL}/api/organizations/{self._org_uuid}/chat_conversations",
            headers=self._headers(accept="application/json"),
            json=body,
            timeout=30,
        )
        if response.status_code not in (200, 201):
            detail = await self._response_detail(response)
            raise HTTPException(status_code=response.status_code, detail=detail)
        return conversation_id

    async def _set_conversation_settings(self) -> None:
        if not self._conversation_id:
            return
        response = await self._client.request(
            "PUT",
            self._conversation_url(),
            headers=self._headers(accept="application/json"),
            json={"settings": {"paprika_mode": None}},
            timeout=30,
        )
        if response.status_code not in (200, 204):
            logger.debug(
                "Claude conversation settings update failed: %s %s",
                response.status_code,
                await self._response_detail(response),
            )

    async def _response_detail(self, response: Any) -> str:
        try:
            if "application/json" in response.headers.get("Content-Type", ""):
                payload = response.json()
                if isinstance(payload, dict):
                    return str(payload.get("detail") or payload.get("error") or payload)
                return str(payload)
            text = await response.atext()
            return text[:300]
        except Exception:
            return "Unexpected Claude upstream error."

    def _headers(self, *, accept: str, include_content_type: bool = True) -> Dict[str, str]:
        headers = {
            "Accept": accept,
            "Origin": _HOST_URL,
            "Referer": f"{_HOST_URL}/"
            if not self._conversation_id
            else f"{_HOST_URL}/chat/{self._conversation_id}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
            "Cookie": self.cookie_header or "",
        }
        if include_content_type:
            headers["Content-Type"] = "application/json"
        return headers

    def _conversation_url(self) -> str:
        return "%s/api/organizations/%s/chat_conversations/%s" % (
            _HOST_URL,
            self._org_uuid,
            self._conversation_id,
        )

    def _conversation_completion_url(self) -> str:
        return self._conversation_url() + "/completion"

    def _estimate_prompt_tokens(self, messages: List[dict]) -> int:
        try:
            merged_text, _images = self._merge_messages(messages)
        except HTTPException:
            return 0
        return _estimate_tokens_from_text(merged_text)

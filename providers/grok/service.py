from __future__ import annotations

import base64
import json
import logging
import os
import random
import re
import string
import time
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import HTTPException

from providers.base import BaseService
from providers.common import Client
from providers.grok.auth import GrokAuth
from providers.grok.types import GrokResponse

logger = logging.getLogger(__name__)

_NEW_CHAT_URL = "https://grok.com/rest/app-chat/conversations/new"
_CONVERSATION_URL = "https://grok.com/rest/app-chat/conversations/"
_UPLOAD_URL = "https://grok.com/rest/app-chat/upload-file"
_MAX_RETRIES = 5
_HOST_URL = "https://grok.com"

_IMAGE_SIGNATURES = {
    b"\xff\xd8\xff": ("jpg", "image/jpeg"),
    b"\x89PNG\r\n\x1a\n": ("png", "image/png"),
    b"GIF89a": ("gif", "image/gif"),
}


def _is_base64_image(s: str) -> bool:
    try:
        decoded = base64.b64decode(s, validate=True)
        return any(decoded.startswith(sig) for sig in _IMAGE_SIGNATURES)
    except Exception:
        return False


def _get_extension_and_mime(data: bytes) -> Tuple[str, str]:
    for sig, (ext, mime) in _IMAGE_SIGNATURES.items():
        if data.startswith(sig):
            return ext, mime
    return "jpg", "image/jpeg"


def _resolve_grok_model(origin_model: str) -> str:
    name = (origin_model or "").lower()
    if "grok-4" in name:
        return "grok-4"
    if "grok-3" in name or "grok-2" in name or "grok-beta" in name:
        return "grok-3"
    return "grok-3"


class GrokService(BaseService):
    def __init__(self, token: Optional[str] = None) -> None:
        super().__init__(token)
        self._auth = GrokAuth()
        self.cookie_header: Optional[str] = None
        self.data: Dict[str, Any] = {}
        self.origin_model = ""
        self.resp_model = ""
        self.max_tokens = 4096
        self._statsig_id: Optional[str] = None
        self._profile: Optional[str] = None
        self._file_attachments: List[str] = []
        self._message_text = ""
        self._conversation_id: Optional[str] = None
        self._parent_response_id: Optional[str] = None
        self._client: Optional[Client] = None

    async def set_dynamic_data(self, data: Dict[str, Any], profile: Optional[str] = None) -> None:
        self.data = data
        self.origin_model = data.get("model", "grok-3")
        self.resp_model = _resolve_grok_model(self.origin_model)
        self.max_tokens = data.get("max_tokens", 4096)
        self._profile = profile

        if self.req_token and self._auth.is_browser_token(self.req_token):
            session = await self._auth.get_browser_session(profile=profile)
            self.cookie_header = session["cookie_header"]
            self._statsig_id = session.get("statsig_id")
        else:
            self.cookie_header = self.req_token
            self._statsig_id = os.getenv("GROK_X_STATSIG_ID", "").strip() or None

        self._conversation_id = data.get("conversation_id")
        self._parent_response_id = data.get("parent_message_id")

        proxy = os.getenv("GROK_PROXY", "").strip() or None
        self._client = Client(proxy=proxy, impersonate="chrome131")

    async def _refresh_session(self) -> None:
        if not (self.req_token and self._auth.is_browser_token(self.req_token)):
            return
        session = await self._auth.get_browser_session(force_refresh=True, profile=self._profile)
        self.cookie_header = session["cookie_header"]
        self._statsig_id = session.get("statsig_id")

    async def get_chat_requirements(self) -> Optional[str]:
        if not self.cookie_header:
            raise HTTPException(status_code=401, detail="No Grok cookies available.")
        return None

    async def prepare_send_conversation(self) -> dict:
        messages = self.data.get("messages", [])
        self._message_text, image_data_list = self._parse_messages(messages)

        if image_data_list:
            self._file_attachments = []
            for img_data in image_data_list:
                file_id = await self._upload_image(img_data)
                if file_id:
                    self._file_attachments.append(file_id)

        return self._build_payload()

    async def send_conversation(self) -> Any:
        payload = self._build_payload()
        headers = self._build_headers()

        result = await self._send_with_retries(payload, headers)
        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=500, detail=result.get("error", "Unknown Grok error"))

        grok_response = GrokResponse(result, enable_artifact_files=False)
        if grok_response.error:
            raise HTTPException(status_code=500, detail=grok_response.error)

        return self._to_openai_dict(grok_response)

    async def close_client(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # Message parsing
    # ------------------------------------------------------------------

    def _parse_messages(self, messages: List[dict]) -> Tuple[str, List[Union[str, BytesIO]]]:
        text_parts: List[str] = []
        image_data_list: List[Union[str, BytesIO]] = []
        last_user_text = ""
        history_lines: List[str] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                history_lines.append(f"system: {content}")
                continue

            if isinstance(content, list):
                msg_text_parts: List[str] = []
                for item in content:
                    if item.get("type") == "text":
                        msg_text_parts.append(item.get("text", ""))
                    elif item.get("type") == "image_url":
                        url = item.get("image_url", {}).get("url", "")
                        img_data = self._decode_image_url(url)
                        if img_data:
                            image_data_list.append(img_data)
                msg_text = " ".join(msg_text_parts)
            else:
                msg_text = str(content)

            if role == "user":
                last_user_text = msg_text
            else:
                history_lines.append(f"{role}: {msg_text}")

        if history_lines:
            text_parts.append("\n".join(history_lines))
        if last_user_text:
            text_parts.append(last_user_text)

        return "\n".join(text_parts), image_data_list

    def _decode_image_url(self, url: str) -> Optional[Union[str, BytesIO]]:
        if url.startswith("data:image"):
            match = re.match(r"data:image/[^;]+;base64,(.+)", url)
            if match:
                b64_data = match.group(1)
                try:
                    raw = base64.b64decode(b64_data)
                    return BytesIO(raw)
                except Exception as exc:
                    logger.warning("Failed to decode base64 image: %s", exc)
                    return None
        elif os.path.exists(url):
            return url
        elif _is_base64_image(url):
            try:
                raw = base64.b64decode(url)
                return BytesIO(raw)
            except Exception:
                pass
        logger.warning("Unsupported image URL format: %s", url[:50])
        return None

    # ------------------------------------------------------------------
    # Payload / headers
    # ------------------------------------------------------------------

    def _build_payload(self) -> dict:
        payload: Dict[str, Any] = {
            "temporary": self.data.get("temporary", False),
            "modelName": self.resp_model,
            "message": self._message_text,
            "fileAttachments": self._file_attachments,
            "imageAttachments": [],
            "disableSearch": self.data.get("disable_search", False),
            "enableImageGeneration": self.data.get("enable_image_generation", True),
            "returnImageBytes": self.data.get("return_image_bytes", False),
            "returnRawGrokInXaiRequest": False,
            "enableImageStreaming": True,
            "imageGenerationCount": self.data.get("image_generation_count", 2),
            "forceConcise": True,
            "toolOverrides": {},
            "enableSideBySide": True,
            "sendFinalMetadata": True,
            "isPreset": False,
            "isReasoning": self.data.get("is_reasoning", False),
            "disableTextFollowUps": True,
            "customInstructions": "",
            "deepsearch preset": "",
            "webpageUrls": [],
            "disableArtifact": False,
            "responseMetadata": {"requestModelDetails": {"modelId": self.resp_model}},
        }
        if self._parent_response_id:
            payload["parentResponseId"] = self._parent_response_id
        return payload

    def _build_headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://grok.com",
            "Referer": "https://grok.com/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Sec-CH-UA": '"Chromium";v="131", "Not:A-Brand";v="24", "Google Chrome";v="131"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
            "Priority": "u=1, i",
        }
        if self._statsig_id:
            headers["x-statsig-id"] = self._statsig_id
        if self.cookie_header:
            headers["Cookie"] = self.cookie_header
        return headers

    # ------------------------------------------------------------------
    # Image upload via HTTP
    # ------------------------------------------------------------------

    async def _upload_image(
        self,
        file_input: Union[str, BytesIO],
        file_extension: Optional[str] = None,
        file_mime_type: Optional[str] = None,
    ) -> Optional[str]:
        if isinstance(file_input, str):
            if os.path.exists(file_input):
                with open(file_input, "rb") as f:
                    file_content = f.read()
            elif _is_base64_image(file_input):
                file_content = base64.b64decode(file_input)
            else:
                raise ValueError("String is neither a valid file path nor a base64 image")
        elif isinstance(file_input, BytesIO):
            file_content = file_input.getvalue()
        else:
            raise ValueError("file_input must be a file path, base64 string, or BytesIO")

        if file_extension is None or file_mime_type is None:
            ext, mime = _get_extension_and_mime(file_content)
            file_extension = file_extension or ext
            file_mime_type = file_mime_type or mime

        file_content_b64 = base64.b64encode(file_content).decode("utf-8")
        file_name_base = file_content_b64[:10].replace("/", "_").replace("+", "_")
        file_name = f"{file_name_base}.{file_extension}"

        payload = {
            "fileName": file_name,
            "fileMimeType": file_mime_type,
            "content": file_content_b64,
        }

        refreshed = False
        for attempt in range(2):
            headers = self._build_headers()
            headers["Content-Type"] = "application/json"

            r = await self._client.post(_UPLOAD_URL, headers=headers, json=payload, timeout=60)
            if r.status_code == 200:
                break

            text = r.text[:500] if hasattr(r, "text") else ""
            cf_challenge = (
                r.status_code in (403, 503)
                and ("Just a moment" in text or "cf-chl" in text or "cloudflare" in text.lower())
            )
            if cf_challenge and not refreshed:
                logger.warning("Grok upload hit Cloudflare challenge; refreshing browser session")
                try:
                    await self._refresh_session()
                except Exception as refresh_exc:
                    raise ValueError(
                        "Image upload blocked by Cloudflare and session refresh failed: %s"
                        % refresh_exc
                    )
                refreshed = True
                continue
            raise ValueError("Image upload failed: HTTP %s - %s" % (r.status_code, text[:200]))

        try:
            resp = r.json()
        except Exception as exc:
            raise ValueError("Image upload response was not JSON: %s" % exc)

        if "fileMetadataId" not in resp:
            raise ValueError("Server response does not contain fileMetadataId: %s" % resp)

        return resp["fileMetadataId"]

    # ------------------------------------------------------------------
    # Send conversation via HTTP
    # ------------------------------------------------------------------

    async def _send_with_retries(self, payload: dict, headers: dict) -> dict:
        target_url = (
            _CONVERSATION_URL + self._conversation_id + "/responses"
            if self._conversation_id
            else _NEW_CHAT_URL
        )

        last_error_data: dict = {}
        rebootstrapped = False

        for try_index in range(_MAX_RETRIES):
            logger.debug("Grok send attempt %s/%s", try_index + 1, _MAX_RETRIES)

            try:
                r = await self._client.post(target_url, headers=headers, json=payload, timeout=120)
            except Exception as exc:
                logger.warning("Grok request error: %s", exc)
                last_error_data = {"error": str(exc)}
                continue

            if r.status_code != 200:
                text = r.text[:500] if hasattr(r, "text") else ""
                last_error_data = {"error": "HTTP %s: %s" % (r.status_code, text)}
                if "Too many requests" in text:
                    logger.warning("Rate limited, retrying...")
                    continue
                if "This service is not available in your region" in text:
                    return last_error_data
                if r.status_code in (401, 403):
                    if rebootstrapped:
                        raise HTTPException(
                            status_code=401,
                            detail="Grok auth failed after refresh: HTTP %s" % r.status_code,
                        )
                    logger.warning(
                        "Grok returned %s; refreshing browser session and retrying",
                        r.status_code,
                    )
                    try:
                        await self._refresh_session()
                    except Exception as refresh_exc:
                        raise HTTPException(
                            status_code=401,
                            detail="Grok session refresh failed: %s" % refresh_exc,
                        )
                    headers = self._build_headers()
                    rebootstrapped = True
                    continue
                continue

            try:
                raw_text = r.text if hasattr(r, "text") else ""
            except Exception:
                raw_text = ""

            parsed = self._parse_ndjson_response(raw_text)
            if parsed and not parsed.get("error"):
                return parsed
            last_error_data = parsed if parsed else {"error": "Empty response"}

        return last_error_data

    def _parse_ndjson_response(self, raw_text: str) -> dict:
        """Parse newline-delimited JSON response from Grok."""
        if not raw_text:
            return {"error": "Empty response body"}

        final_dict: dict = {}
        conversation_info: dict = {}
        new_title: Optional[str] = None

        for line in raw_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
                if "modelResponse" in parsed.get("result", {}):
                    parsed["result"]["response"] = {
                        "modelResponse": parsed["result"].pop("modelResponse")
                    }
                if "conversation" in parsed.get("result", {}):
                    conversation_info = parsed["result"]["conversation"]
                if "title" in parsed.get("result", {}):
                    new_title = parsed["result"]["title"].get("newTitle")
                if "modelResponse" in parsed.get("result", {}).get("response", {}):
                    final_dict = parsed
                elif "modelResponse" in parsed.get("result", {}):
                    parsed["result"]["response"] = conversation_info
            except (json.JSONDecodeError, KeyError):
                continue

        if final_dict:
            model_response = final_dict["result"]["response"]["modelResponse"]
            final_dict["result"]["response"] = {"modelResponse": model_response}
            final_dict["result"]["response"]["conversationId"] = conversation_info.get(
                "conversationId"
            )
            final_dict["result"]["response"]["title"] = conversation_info.get("title")
            final_dict["result"]["response"]["createTime"] = conversation_info.get("createTime")
            final_dict["result"]["response"]["modifyTime"] = conversation_info.get("modifyTime")
            final_dict["result"]["response"]["temporary"] = conversation_info.get("temporary")
            final_dict["result"]["response"]["newTitle"] = new_title
            return final_dict

        return {"error": "No modelResponse found in response"}

    # ------------------------------------------------------------------
    # OpenAI format conversion
    # ------------------------------------------------------------------

    def _to_openai_dict(self, grok_response: GrokResponse) -> dict:
        content = grok_response.modelResponse.message or ""
        completion_tokens = len(content.split()) if content else 0
        prompt_tokens = len(self._message_text.split()) if self._message_text else 0

        image_urls = [img.full_url for img in grok_response.modelResponse.generatedImages]
        if image_urls:
            content += "\n\nGenerated images:\n" + "\n".join(image_urls)

        return {
            "id": "chatcmpl-"
            + "".join(random.choice(string.ascii_letters + string.digits) for _ in range(29)),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.resp_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "logprobs": None,
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

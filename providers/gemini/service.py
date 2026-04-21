from __future__ import annotations

import base64
import logging
import os
import re
import tempfile
from http.cookies import SimpleCookie
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from providers.base import BaseService
from providers.gemini.auth import GeminiAuth
from providers.gemini.formatting import GeminiFormatter

logger = logging.getLogger(__name__)

_PROXY_URL = os.getenv("GEMINI_PROXY", "").strip() or os.getenv("PROXY_URL", "").strip() or None
_IMAGE_MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "application/pdf": "pdf",
}

try:
    from gemini_webapi import GeminiClient
except ImportError:
    GeminiClient = None


def _estimate_tokens_from_text(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _message_prefix(role: str) -> str:
    if role == "assistant":
        return "Assistant"
    return "User"


def _parse_cookie_header(cookie_header: str) -> Dict[str, str]:
    cookie = SimpleCookie()
    cookie.load(cookie_header or "")
    parsed = {name: morsel.value for name, morsel in cookie.items()}
    if parsed:
        return parsed

    result: Dict[str, str] = {}
    for part in (cookie_header or "").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        result[name.strip()] = value.strip()
    return result


class GeminiService(BaseService):
    def __init__(self, token: Optional[str] = None) -> None:
        super().__init__(token)
        self._auth = GeminiAuth()
        self._formatter = GeminiFormatter()
        self._profile: Optional[str] = None
        self._client: Any = None

        self.data: Dict[str, Any] = {}
        self.origin_model = ""
        self.resp_model = ""
        self.max_tokens = 4096
        self.prompt_tokens = 0
        self.secure_1psid: Optional[str] = None
        self.secure_1psidts: Optional[str] = None

        self._prepared_payload: Optional[Dict[str, Any]] = None
        self._temp_files: List[str] = []

    async def set_dynamic_data(self, data: Dict[str, Any], profile: Optional[str] = None) -> None:
        self.data = data
        self._profile = profile
        self.origin_model = data.get("model", "gemini-2.5-flash")
        self.resp_model = self.origin_model
        self.max_tokens = data.get("max_tokens", 4096)
        if not isinstance(self.max_tokens, int):
            self.max_tokens = 4096

        if self.req_token and self._auth.is_browser_token(self.req_token):
            session = await self._auth.get_browser_session(profile=profile)
            self.secure_1psid = session.get("secure_1psid")
            self.secure_1psidts = session.get("secure_1psidts")
        elif self.req_token:
            self.secure_1psid, self.secure_1psidts = self._parse_credentials_from_token(
                self.req_token
            )

        if not self.secure_1psid:
            self.secure_1psid = os.getenv("GEMINI_SECURE_1PSID", "").strip() or None
        if self.secure_1psidts is None:
            self.secure_1psidts = os.getenv("GEMINI_SECURE_1PSIDTS", "").strip() or None

    async def get_chat_requirements(self) -> Optional[str]:
        self._validate_request()

        if GeminiClient is None:
            raise HTTPException(
                status_code=500,
                detail="Gemini support requires gemini-webapi>=2.0.0 to be installed.",
            )
        if not self.secure_1psid:
            raise HTTPException(
                status_code=401,
                detail=(
                    "No Gemini cookies available. Use `Bearer gemini-browser` or set "
                    "GEMINI_SECURE_1PSID / GEMINI_COOKIE."
                ),
            )

        self._client = GeminiClient(self.secure_1psid, self.secure_1psidts or "", proxy=_PROXY_URL)
        try:
            await self._client.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Failed to initialize Gemini session: %s" % exc)

        return None

    async def prepare_send_conversation(self) -> dict:
        prompt, files = self._merge_messages(self.data.get("messages", []))
        self.prompt_tokens = _estimate_tokens_from_text(prompt)
        self._prepared_payload = {
            "prompt": prompt,
            "files": files,
            "model": self.resp_model,
            "temporary": bool(self.data.get("temporary", False)),
        }
        return self._prepared_payload

    async def send_conversation(self) -> Any:
        if not self._client:
            await self.get_chat_requirements()
        if not self._prepared_payload:
            await self.prepare_send_conversation()

        prompt = self._prepared_payload["prompt"]
        files = self._prepared_payload["files"] or None
        model = self._prepared_payload["model"]
        temporary = self._prepared_payload["temporary"]

        try:
            if self.data.get("stream", False):
                response = self._client.generate_content_stream(
                    prompt,
                    files=files,
                    model=model,
                    temporary=temporary,
                )
                return self._formatter.stream_response(
                    self,
                    response,
                    self.resp_model,
                    self.max_tokens,
                )

            response = await self._client.generate_content(
                prompt,
                files=files,
                model=model,
                temporary=temporary,
            )
            text = self._response_text(response)
            return self._formatter.build_response(
                text=text,
                prompt_tokens=self.prompt_tokens,
                max_tokens=self.max_tokens,
                model=self.resp_model,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Gemini request failed: %s" % exc)

    async def close_client(self) -> None:
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if close:
                try:
                    await close()
                except Exception as exc:
                    logger.warning("Gemini client cleanup failed: %s", exc)
            self._client = None

        while self._temp_files:
            path = self._temp_files.pop()
            try:
                os.remove(path)
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.warning("Failed to remove Gemini temp file %s: %s", path, exc)

    def _validate_request(self) -> None:
        if self.data.get("tools") or self.data.get("tool_choice"):
            raise HTTPException(
                status_code=400,
                detail="Gemini web tool calling is not implemented on this endpoint.",
            )

        if self.data.get("n") not in (None, 1):
            raise HTTPException(
                status_code=400,
                detail="Gemini web only supports a single completion on this endpoint.",
            )

        if not self.data.get("messages"):
            raise HTTPException(status_code=400, detail="Gemini request must include messages.")

    def _parse_credentials_from_token(self, token: str) -> Tuple[Optional[str], Optional[str]]:
        stripped = token.strip()
        if not stripped:
            return None, None

        if "__Secure-1PSID=" in stripped:
            cookies = _parse_cookie_header(stripped)
            return cookies.get("__Secure-1PSID"), cookies.get("__Secure-1PSIDTS")

        if "|" in stripped:
            secure_1psid, secure_1psidts = stripped.split("|", 1)
            return secure_1psid.strip() or None, secure_1psidts.strip() or None

        if ":" in stripped:
            secure_1psid, secure_1psidts = stripped.split(":", 1)
            return secure_1psid.strip() or None, secure_1psidts.strip() or None

        return stripped, None

    def _merge_messages(self, messages: List[dict]) -> Tuple[str, List[str]]:
        system_parts: List[str] = []
        dialogue_parts: List[str] = []
        files: List[str] = []

        for message in messages:
            role = message.get("role", "user")
            if role not in {"system", "user", "assistant"}:
                raise HTTPException(status_code=400, detail="Unsupported Gemini role: %s" % role)

            text, message_files = self._parse_message_content(message.get("content", ""))
            files.extend(message_files)
            if role == "system":
                if text:
                    system_parts.append(text)
                continue
            if text:
                dialogue_parts.append("%s: %s" % (_message_prefix(role), text))

        prompt_parts: List[str] = []
        if system_parts:
            prompt_parts.append("System:\n%s" % "\n".join(system_parts))
        if dialogue_parts:
            prompt_parts.append("\n\n".join(dialogue_parts))

        prompt = "\n\n".join(part for part in prompt_parts if part).strip()
        if not prompt and files:
            prompt = "Please analyze the attached files."
        if not prompt:
            raise HTTPException(status_code=400, detail="Gemini request produced an empty prompt.")
        return prompt, files

    def _parse_message_content(self, content: Any) -> Tuple[str, List[str]]:
        if isinstance(content, str):
            return content, []

        if not isinstance(content, list):
            return str(content or ""), []

        text_parts: List[str] = []
        files: List[str] = []
        for item in content:
            item_type = item.get("type")
            if item_type == "text":
                text_parts.append(item.get("text", ""))
                continue

            if item_type == "image_url":
                url = item.get("image_url", {}).get("url", "")
                files.append(self._materialize_file(url))
                continue

            raise HTTPException(
                status_code=400,
                detail="Unsupported Gemini content item type: %s" % item_type,
            )

        return " ".join(part for part in text_parts if part).strip(), files

    def _materialize_file(self, url: str) -> str:
        if not url:
            raise HTTPException(status_code=400, detail="Gemini image_url item is missing a URL.")

        if url.startswith("data:"):
            match = re.match(r"data:([^;]+);base64,(.+)", url, re.DOTALL)
            if not match:
                raise HTTPException(status_code=400, detail="Gemini only supports base64 data URLs.")
            media_type, b64_data = match.groups()
            try:
                raw = base64.b64decode(b64_data)
            except Exception as exc:
                raise HTTPException(status_code=400, detail="Invalid Gemini data URL: %s" % exc)
            return self._write_temp_file(raw, media_type)

        if os.path.exists(url):
            return url

        raise HTTPException(
            status_code=400,
            detail="Gemini only supports local file paths or base64 data URLs for images.",
        )

    def _write_temp_file(self, raw: bytes, media_type: str) -> str:
        suffix = "." + _IMAGE_MIME_TO_EXT.get(media_type.lower(), "bin")
        fd, path = tempfile.mkstemp(prefix="gemini-upload-", suffix=suffix)
        os.close(fd)
        with open(path, "wb") as file_obj:
            file_obj.write(raw)
        self._temp_files.append(path)
        return path

    def _response_text(self, response: Any) -> str:
        text = getattr(response, "text", "") or ""
        if text:
            return text

        images = getattr(response, "images", None) or []
        if images:
            image_lines = []
            for image in images:
                title = getattr(image, "title", "") or "image"
                url = getattr(image, "url", "")
                if url:
                    image_lines.append("%s: %s" % (title, url))
            if image_lines:
                return "\n".join(image_lines)

        return ""

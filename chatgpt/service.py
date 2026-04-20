from __future__ import annotations

import json
import logging
import os
import random
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from chatgpt.browser_auth import get_access_token_from_browser, is_browser_auth_token
from chatgpt.client import Client
from chatgpt.formatting import (
    api_messages_to_chat,
    format_not_stream_response,
    head_process_response,
    stream_response,
)
from chatgpt.fp import generate_fingerprint
from chatgpt.pow import get_answer_token, get_config, get_dpl, get_requirements_token

logger = logging.getLogger(__name__)

_HISTORY_DISABLED = os.getenv("HISTORY_DISABLED", "true").lower() in ("true", "1", "t", "y", "yes")
_CONVERSATION_ONLY = os.getenv("CONVERSATION_ONLY", "false").lower() in (
    "true",
    "1",
    "t",
    "y",
    "yes",
)
_PROXY_URL = os.getenv("PROXY_URL", "").strip() or None
_HOST_URL = os.getenv("CHATGPT_BASE_URL", "https://chatgpt.com").rstrip("/")
_TURNSTILE_SOLVER_URL = os.getenv("TURNSTILE_SOLVER_URL", "").strip() or None
_ARKOSE_TOKEN_URL = os.getenv("ARKOSE_TOKEN_URL", "").strip() or None

_MODEL_MAP = {
    "o3-mini-high": "o3-mini-high",
    "o3-mini-medium": "o3-mini-medium",
    "o3-mini-low": "o3-mini-low",
    "o3-mini": "o3-mini",
    "o3": "o3",
    "o1-preview": "o1-preview",
    "o1-pro": "o1-pro",
    "o1-mini": "o1-mini",
    "o1": "o1",
    "gpt-4.5o": "gpt-4.5o",
    "gpt-4o-canmore": "gpt-4o-canmore",
    "gpt-4o-mini": "gpt-4o-mini",
    "gpt-4o": "gpt-4o",
    "gpt-4-mobile": "gpt-4-mobile",
    "gpt-4": "gpt-4",
    "gpt-3.5": "text-davinci-002-render-sha",
    "auto": "auto",
}


def _resolve_model(origin_model: str) -> str:
    for key, value in _MODEL_MAP.items():
        if key in origin_model:
            return value
    return "gpt-4o"


class ChatService:
    def __init__(self, token: Optional[str] = None) -> None:
        self.req_token = token
        self.access_token: Optional[str] = None
        self.account_id: Optional[str] = None
        self.chat_token = "gAAAAAB"
        self.s: Optional[Client] = None
        self.ss: Optional[Client] = None
        self.proof_token: Optional[str] = None
        self.ark0se_token: Optional[str] = None
        self.turnstile_token: Optional[str] = None
        self.data: Dict[str, Any] = {}
        self.origin_model = ""
        self.resp_model = ""
        self.req_model = ""
        self.gizmo_id: Optional[str] = None
        self.api_messages: list = []
        self.prompt_tokens = 0
        self.max_tokens = 2147483647
        self.history_disabled = _HISTORY_DISABLED
        self.host_url = _HOST_URL
        self.base_headers: Dict[str, str] = {}
        self.base_url = ""
        self.chat_headers: Optional[Dict[str, str]] = None
        self.chat_request: Optional[Dict[str, Any]] = None
        self.user_agent = ""
        self.impersonate = "chrome120"

    async def set_dynamic_data(self, data: Dict[str, Any]) -> None:
        if self.req_token:
            if is_browser_auth_token(self.req_token):
                self.access_token = await get_access_token_from_browser()
            elif self.req_token.startswith("eyJhbGciOi") or self.req_token.startswith("fk-"):
                self.access_token = self.req_token
            else:
                self.access_token = self.req_token
        else:
            self.access_token = None

        self.account_id = data.get("Chatgpt-Account-Id")

        fp = generate_fingerprint()
        self.user_agent = fp.pop(
            "user-agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        )
        self.impersonate = fp.pop("impersonate", "chrome120")

        self.data = data
        self._set_model()

        self.api_messages = data.get("messages", [])
        self.max_tokens = data.get("max_tokens", 2147483647)
        if not isinstance(self.max_tokens, int):
            self.max_tokens = 2147483647
        self.history_disabled = data.get("history_disabled", _HISTORY_DISABLED)

        proxy = _PROXY_URL
        self.s = Client(proxy=proxy, impersonate=self.impersonate)
        self.ss = self.s

        self.base_headers = {
            "accept": "*/*",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "oai-language": "en-US",
            "origin": self.host_url,
            "priority": "u=1, i",
            "referer": "%s/" % self.host_url,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        self.base_headers.update(fp)

        if self.access_token:
            self.base_url = self.host_url + "/backend-api"
            self.base_headers["authorization"] = "Bearer %s" % self.access_token
            if self.account_id:
                self.base_headers["chatgpt-account-id"] = self.account_id
        else:
            self.base_url = self.host_url + "/backend-anon"

        logger.info(
            "Model: %s -> %s, UA: %s" % (self.origin_model, self.req_model, self.user_agent[:50])
        )
        await get_dpl(self)

    def _set_model(self) -> None:
        self.origin_model = self.data.get("model", "gpt-4o")
        self.resp_model = self.origin_model
        if "gizmo" in self.origin_model or "g-" in self.origin_model:
            self.gizmo_id = "g-" + self.origin_model.split("g-")[-1]
        else:
            self.gizmo_id = None
        self.req_model = _resolve_model(self.origin_model)

    async def get_chat_requirements(self) -> Optional[str]:
        if _CONVERSATION_ONLY:
            return None
        url = "%s/sentinel/chat-requirements" % self.base_url
        headers = self.base_headers.copy()
        try:
            config = get_config(self.user_agent, self.req_token)
            p = get_requirements_token(config)
            r = await self.ss.post(url, headers=headers, json={"p": p}, timeout=5)
            if r.status_code != 200:
                if r.headers.get("Content-Type", "") == "application/json":
                    detail = r.json().get("detail", r.json())
                else:
                    detail = r.text[:200]
                raise HTTPException(status_code=r.status_code, detail=detail)

            resp = r.json()
            self.proof_token = None
            self.ark0se_token = None
            self.turnstile_token = None

            turnstile = resp.get("turnstile", {})
            if turnstile.get("required"):
                turnstile_dx = turnstile.get("dx", "")
                try:
                    if _TURNSTILE_SOLVER_URL:
                        solver_res = await self.s.post(
                            _TURNSTILE_SOLVER_URL,
                            json={
                                "url": "https://chatgpt.com",
                                "p": p,
                                "dx": turnstile_dx,
                                "ua": self.user_agent,
                            },
                            timeout=15,
                        )
                        self.turnstile_token = solver_res.json().get("t")
                except Exception as e:
                    logger.info("Turnstile ignored: %s" % e)

            arkose = resp.get("arkose", {})
            if arkose.get("required"):
                arkose_dx = arkose.get("dx", "")
                if _ARKOSE_TOKEN_URL:
                    try:
                        arkose_client = Client(impersonate=self.impersonate)
                        r2 = await arkose_client.post(
                            _ARKOSE_TOKEN_URL,
                            json={"blob": arkose_dx, "method": "chat4"},
                            timeout=15,
                        )
                        r2esp = r2.json()
                        if r2esp.get("solved", True):
                            self.ark0se_token = r2esp.get("token")
                        await arkose_client.close()
                    except Exception as e:
                        logger.info("Arkose ignored: %s" % e)
                else:
                    logger.info(
                        "Arkose required but no ARKOSE_TOKEN_URL configured, proceeding without"
                    )

            proofofwork = resp.get("proofofwork", {})
            if proofofwork.get("required"):
                pow_diff = proofofwork.get("difficulty", "")
                pow_seed = proofofwork.get("seed", "")
                self.proof_token, solved = await run_in_threadpool(
                    get_answer_token, pow_seed, pow_diff, config
                )
                if not solved:
                    raise HTTPException(status_code=403, detail="Failed to solve proof of work")

            self.chat_token = resp.get("token")
            if not self.chat_token:
                raise HTTPException(status_code=403, detail="Failed to get chat token")
            return self.chat_token
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def prepare_send_conversation(self) -> dict:
        try:
            chat_messages = api_messages_to_chat(self.api_messages)
        except Exception as e:
            logger.error("Failed to format messages: %s" % e)
            raise HTTPException(status_code=400, detail="Failed to format messages.")

        self.chat_headers = self.base_headers.copy()
        self.chat_headers["accept"] = "text/event-stream"
        self.chat_headers["openai-sentinel-chat-requirements-token"] = self.chat_token
        if self.proof_token:
            self.chat_headers["openai-sentinel-proof-token"] = self.proof_token
        if self.ark0se_token:
            self.chat_headers["openai-sentinel-arkose-token"] = self.ark0se_token
        if self.turnstile_token:
            self.chat_headers["openai-sentinel-turnstile-token"] = self.turnstile_token

        if _CONVERSATION_ONLY:
            self.chat_headers.pop("openai-sentinel-chat-requirements-token", None)
            self.chat_headers.pop("openai-sentinel-proof-token", None)
            self.chat_headers.pop("openai-sentinel-arkose-token", None)
            self.chat_headers.pop("openai-sentinel-turnstile-token", None)

        if self.gizmo_id:
            conversation_mode = {"kind": "gizmo_interaction", "gizmo_id": self.gizmo_id}
        else:
            conversation_mode = {"kind": "primary_assistant"}

        self.chat_request = {
            "action": "next",
            "client_contextual_info": {
                "is_dark_mode": False,
                "time_since_loaded": random.randint(50, 500),
                "page_height": random.randint(500, 1000),
                "page_width": random.randint(1000, 2000),
                "pixel_ratio": 1.5,
                "screen_height": random.randint(800, 1200),
                "screen_width": random.randint(1200, 2200),
            },
            "conversation_mode": conversation_mode,
            "conversation_origin": None,
            "force_paragen": False,
            "force_paragen_model_slug": "",
            "force_rate_limit": False,
            "force_use_sse": True,
            "history_and_training_disabled": self.history_disabled,
            "messages": chat_messages,
            "model": self.req_model,
            "paragen_cot_summary_display_override": "allow",
            "paragen_stream_type_override": None,
            "parent_message_id": str(uuid.uuid4()),
            "reset_rate_limits": False,
            "suggestions": [],
            "supported_encodings": [],
            "system_hints": [],
            "timezone": "America/Los_Angeles",
            "timezone_offset_min": -480,
            "variant_purpose": "comparison_implicit",
            "websocket_request_id": str(uuid.uuid4()),
        }
        conv_id = self.data.get("conversation_id")
        if conv_id:
            self.chat_request["conversation_id"] = conv_id
        parent_msg_id = self.data.get("parent_message_id")
        if parent_msg_id:
            self.chat_request["parent_message_id"] = parent_msg_id

        return self.chat_request

    async def send_conversation(self) -> Any:
        try:
            url = "%s/conversation" % self.base_url
            stream = self.data.get("stream", False)
            r = await self.s.post_stream(
                url, headers=self.chat_headers, json=self.chat_request, timeout=120, stream=True
            )
            if r.status_code != 200:
                try:
                    rtext = await r.atext()
                except Exception:
                    rtext = ""
                if r.headers.get("Content-Type", "") == "application/json" and rtext:
                    detail = r.json().get("detail", r.json())
                else:
                    if "cf_chl_opt" in rtext:
                        raise HTTPException(status_code=r.status_code, detail="cf_chl_opt")
                    if r.status_code == 429:
                        raise HTTPException(status_code=429, detail="rate-limit")
                    detail = rtext[:200]
                raise HTTPException(status_code=r.status_code, detail=detail)

            content_type = r.headers.get("Content-Type", "")
            if "text/event-stream" in content_type:
                res, started = await head_process_response(r.aiter_lines())
                if not started:
                    raise HTTPException(
                        status_code=403,
                        detail="Our systems have detected unusual activity. Please try again later.",
                    )
                if stream:
                    return stream_response(self, res, self.resp_model, self.max_tokens)
                else:
                    return await format_not_stream_response(
                        stream_response(self, res, self.resp_model, self.max_tokens),
                        self.prompt_tokens,
                        self.max_tokens,
                        self.resp_model,
                    )
            elif "application/json" in content_type:
                try:
                    rtext = await r.atext()
                    resp = json.loads(rtext)
                except Exception:
                    resp = {}
                raise HTTPException(status_code=r.status_code, detail=resp)
            else:
                try:
                    rtext = await r.atext()
                except Exception:
                    rtext = ""
                raise HTTPException(status_code=r.status_code, detail=rtext[:200])
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def close_client(self) -> None:
        if self.s:
            await self.s.close()
            self.s = None
        if self.ss and self.ss is not self.s:
            await self.ss.close()
            self.ss = None

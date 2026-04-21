from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request

from providers.base import BaseAuth
from providers.common import (
    Client,
    call_cookie_loader,
    cookie_header_from_jar,
    get_browser_order,
    iter_chromium_cookie_files,
    jwt_expires_at,
    load_browser_cookie3,
    mask_token,
)

logger = logging.getLogger(__name__)

BROWSER_TOKEN_ALIASES = {"browser", "chatgpt-browser", "cookies", "chatgpt-cookies"}

_CHATGPT_DOMAINS = {
    "chatgpt.com",
    ".chatgpt.com",
    "chat.openai.com",
    ".chat.openai.com",
}
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_ACCESS_TOKEN_CACHE: Dict[str, Any] = {}
_ACCESS_TOKEN_CACHE_MULTI: Dict[str, Dict[str, Any]] = {}

_CHATGPT_BROWSER_AUTH = os.getenv("CHATGPT_BROWSER_AUTH", "false").lower() in (
    "true",
    "1",
    "t",
    "y",
    "yes",
)
_CHATGPT_BROWSER_AUTH_ALLOW_REMOTE = os.getenv(
    "CHATGPT_BROWSER_AUTH_ALLOW_REMOTE", "false"
).lower() in ("true", "1", "t", "y", "yes")


def _is_chatgpt_domain(domain: str) -> bool:
    normalized = (domain or "").lower()
    if normalized in _CHATGPT_DOMAINS:
        return True
    return normalized.endswith(".chatgpt.com") or normalized.endswith(".chat.openai.com")


class ChatGPTAuth(BaseAuth):
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

    def ensure_browser_auth_request_allowed(self, request: Optional[Request] = None) -> None:
        if not _CHATGPT_BROWSER_AUTH:
            raise HTTPException(
                status_code=403,
                detail="ChatGPT browser cookie auth is disabled. Set CHATGPT_BROWSER_AUTH=true to enable it.",
            )

        if _CHATGPT_BROWSER_AUTH_ALLOW_REMOTE or request is None:
            return

        host = request.client.host if request.client else ""
        if host not in _LOOPBACK_HOSTS:
            raise HTTPException(
                status_code=403,
                detail=(
                    "ChatGPT browser cookie auth is local-only by default. "
                    "Set CHATGPT_BROWSER_AUTH_ALLOW_REMOTE=true to allow remote callers."
                ),
            )

    def _cookie_header_from_env(self) -> Optional[str]:
        return (
            os.getenv("CHATGPT_COOKIE", "").strip()
            or os.getenv("CHATGPT_COOKIE_STRING", "").strip()
            or None
        )

    def extract_cookie_header_from_browser(self) -> Tuple[Optional[str], List[str]]:
        env_cookie = self._cookie_header_from_env()
        if env_cookie:
            logger.info("Loaded ChatGPT cookies from CHATGPT_COOKIE environment")
            return env_cookie, []

        browser_cookie3 = load_browser_cookie3()
        if browser_cookie3 is None:
            return None, ["browser-cookie3 is not installed"]

        browser_loaders = {
            "arc": getattr(browser_cookie3, "arc", None),
            "chrome": getattr(browser_cookie3, "chrome", None),
            "edge": getattr(browser_cookie3, "edge", None),
            "firefox": getattr(browser_cookie3, "firefox", None),
            "brave": getattr(browser_cookie3, "brave", None),
        }
        diagnostics: List[str] = []

        for browser_name in get_browser_order("CHATGPT_BROWSER"):
            loader = browser_loaders.get(browser_name)
            if loader is None:
                continue

            if browser_name == "firefox":
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "chatgpt.com"),
                        "%s(default)" % browser_name,
                        _is_chatgpt_domain,
                    )
                    if cookie_header:
                        return cookie_header, diagnostics
                except Exception as exc:
                    diagnostics.append("%s=%s: %s" % (browser_name, type(exc).__name__, exc))
                continue

            cookie_files = iter_chromium_cookie_files(browser_name, "CHATGPT_CHROME_PROFILE")
            if not cookie_files:
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "chatgpt.com"),
                        "%s(default)" % browser_name,
                        _is_chatgpt_domain,
                    )
                    if cookie_header:
                        return cookie_header, diagnostics
                except Exception as exc:
                    diagnostics.append("%s=%s: %s" % (browser_name, type(exc).__name__, exc))
                continue

            for cookie_file in cookie_files:
                profile = os.path.basename(os.path.dirname(cookie_file))
                if profile == "Network":
                    profile = os.path.basename(os.path.dirname(os.path.dirname(cookie_file)))
                source = "%s[%s]" % (browser_name, profile)
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "chatgpt.com", cookie_file=cookie_file),
                        source,
                        _is_chatgpt_domain,
                    )
                    if cookie_header:
                        return cookie_header, diagnostics
                except Exception as exc:
                    diagnostics.append("%s=%s: %s" % (source, type(exc).__name__, exc))

        return None, diagnostics

    def extract_all_cookie_headers(self) -> List[Tuple[str, str, str]]:
        env_cookie = self._cookie_header_from_env()
        if env_cookie:
            logger.info("Loaded ChatGPT cookies from CHATGPT_COOKIE environment")
            return [("env", env_cookie, "env")]

        browser_cookie3 = load_browser_cookie3()
        if browser_cookie3 is None:
            return []

        browser_loaders = {
            "arc": getattr(browser_cookie3, "arc", None),
            "chrome": getattr(browser_cookie3, "chrome", None),
            "edge": getattr(browser_cookie3, "edge", None),
            "firefox": getattr(browser_cookie3, "firefox", None),
            "brave": getattr(browser_cookie3, "brave", None),
        }

        results: List[Tuple[str, str, str]] = []

        for browser_name in get_browser_order("CHATGPT_BROWSER"):
            loader = browser_loaders.get(browser_name)
            if loader is None:
                continue

            if browser_name == "firefox":
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "chatgpt.com"),
                        "%s(default)" % browser_name,
                        _is_chatgpt_domain,
                    )
                    if cookie_header:
                        profile_key = "%s[default]" % browser_name
                        results.append((profile_key, cookie_header, "%s(default)" % browser_name))
                except Exception as exc:
                    logger.debug("firefox cookie extraction failed: %s" % exc)
                continue

            cookie_files = iter_chromium_cookie_files(browser_name, "CHATGPT_CHROME_PROFILE")
            if not cookie_files:
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "chatgpt.com"),
                        "%s(default)" % browser_name,
                        _is_chatgpt_domain,
                    )
                    if cookie_header:
                        profile_key = "%s[default]" % browser_name
                        results.append((profile_key, cookie_header, "%s(default)" % browser_name))
                except Exception as exc:
                    logger.debug("%s default cookie extraction failed: %s" % (browser_name, exc))
                continue

            for cookie_file in cookie_files:
                profile = os.path.basename(os.path.dirname(cookie_file))
                if profile == "Network":
                    profile = os.path.basename(os.path.dirname(os.path.dirname(cookie_file)))
                source = "%s[%s]" % (browser_name, profile)
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "chatgpt.com", cookie_file=cookie_file),
                        source,
                        _is_chatgpt_domain,
                    )
                    if cookie_header:
                        results.append((source, cookie_header, source))
                except Exception as exc:
                    logger.debug("%s cookie extraction failed: %s" % (source, exc))

        return results

    async def _fetch_session_for_cookie(
        self, cookie_header: str, profile_key: str
    ) -> Dict[str, Any]:
        cookie_hash = hashlib.sha256(cookie_header.encode("utf-8")).hexdigest()
        host_url = os.getenv("CHATGPT_BROWSER_AUTH_BASE_URL", "https://chatgpt.com").rstrip("/")
        client = Client(impersonate="chrome120")
        headers = {
            "accept": "application/json",
            "cookie": cookie_header,
            "referer": "%s/" % host_url,
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        try:
            response = await client.get(
                "%s/api/auth/session" % host_url, headers=headers, timeout=15
            )
            if response.status_code in (401, 403):
                raise HTTPException(
                    status_code=401,
                    detail="ChatGPT browser cookies are expired or invalid (profile: %s)."
                    % profile_key,
                )
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to fetch ChatGPT session from browser cookies (profile: %s)."
                    % profile_key,
                )

            try:
                session = response.json()
            except Exception:
                raise HTTPException(
                    status_code=502, detail="ChatGPT session response was not JSON."
                )

            access_token = session.get("accessToken")
            if not access_token:
                raise HTTPException(
                    status_code=401,
                    detail="ChatGPT session did not include an accessToken (profile: %s)."
                    % profile_key,
                )

            expires_at = jwt_expires_at(access_token) or int(time.time()) + 600
            result = {
                "profile_key": profile_key,
                "access_token": access_token,
                "access_token_preview": mask_token(access_token),
                "cookie_hash": cookie_hash,
                "expires_at": expires_at,
                "user": session.get("user"),
            }
            logger.info(
                "Loaded ChatGPT access token for profile '%s': %s"
                % (profile_key, mask_token(access_token))
            )
            return result
        finally:
            await client.close()

    async def get_all_sessions(self, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        all_cookies = self.extract_all_cookie_headers()
        if not all_cookies:
            raise HTTPException(
                status_code=401,
                detail="No ChatGPT cookies found. Log in to chatgpt.com in a browser first.",
            )

        results: Dict[str, Dict[str, Any]] = {}
        for profile_key, cookie_header, source in all_cookies:
            cookie_hash = hashlib.sha256(cookie_header.encode("utf-8")).hexdigest()
            if not force_refresh and self._session_cache_valid_multi(profile_key, cookie_hash):
                results[profile_key] = _ACCESS_TOKEN_CACHE_MULTI[profile_key].copy()
                continue
            try:
                session = await self._fetch_session_for_cookie(cookie_header, profile_key)
                _ACCESS_TOKEN_CACHE_MULTI[profile_key] = session.copy()
                results[profile_key] = session
            except Exception as e:
                logger.warning("Skipping profile '%s': %s" % (profile_key, e))
                results[profile_key] = {"profile_key": profile_key, "error": str(e)}

        return results

    async def get_browser_session(
        self, force_refresh: bool = False, profile: Optional[str] = None
    ) -> Dict[str, Any]:
        if profile:
            all_results = await self.get_all_sessions(force_refresh=force_refresh)
            if profile not in all_results:
                available = list(all_results.keys())
                raise HTTPException(
                    status_code=404,
                    detail="Profile '%s' not found. Available profiles: %s" % (profile, available),
                )
            result = all_results[profile]
            if "error" in result:
                raise HTTPException(status_code=401, detail=result["error"])
            return result

        cookie_header, diagnostics = self.extract_cookie_header_from_browser()
        if not cookie_header:
            detail = "No ChatGPT cookies found. Log in to chatgpt.com in Arc/Chrome/Edge/Firefox/Brave first."
            if diagnostics:
                detail += " Diagnostics: " + "; ".join(diagnostics[:5])
            raise HTTPException(status_code=401, detail=detail)

        cookie_hash = hashlib.sha256(cookie_header.encode("utf-8")).hexdigest()
        if not force_refresh and self._session_cache_valid(cookie_hash):
            return _ACCESS_TOKEN_CACHE.copy()

        session = await self._fetch_session_for_cookie(cookie_header, "default")
        _ACCESS_TOKEN_CACHE.clear()
        _ACCESS_TOKEN_CACHE.update(session)
        return _ACCESS_TOKEN_CACHE.copy()

    async def get_access_token(self, profile: Optional[str] = None) -> str:
        session = await self.get_browser_session(profile=profile)
        return session["access_token"]

    def _session_cache_valid(self, cookie_hash: str) -> bool:
        if not _ACCESS_TOKEN_CACHE:
            return False
        if _ACCESS_TOKEN_CACHE.get("cookie_hash") != cookie_hash:
            return False
        expires_at = _ACCESS_TOKEN_CACHE.get("expires_at") or 0
        return int(time.time()) < int(expires_at) - 60

    def _session_cache_valid_multi(self, profile_key: str, cookie_hash: str) -> bool:
        cache = _ACCESS_TOKEN_CACHE_MULTI.get(profile_key)
        if not cache:
            return False
        if cache.get("cookie_hash") != cookie_hash:
            return False
        expires_at = cache.get("expires_at") or 0
        return int(time.time()) < int(expires_at) - 60


# Backward-compatible module-level aliases (for tests and legacy consumers)
from providers.common.utils import jwt_expires_at, mask_token  # noqa: E402
from providers.common.browser import get_browser_order, load_browser_cookie3  # noqa: E402

_load_browser_cookie3 = load_browser_cookie3

_auth_instance = ChatGPTAuth()

is_browser_auth_token = _auth_instance.is_browser_token
parse_browser_token = _auth_instance.parse_browser_token
ensure_browser_auth_request_allowed = _auth_instance.ensure_browser_auth_request_allowed
extract_cookie_header_from_browser = _auth_instance.extract_cookie_header_from_browser
extract_all_cookie_headers = _auth_instance.extract_all_cookie_headers
get_access_token_from_browser = _auth_instance.get_access_token
get_browser_session = _auth_instance.get_browser_session
get_all_browser_sessions = _auth_instance.get_all_sessions


def _get_browser_order():
    return get_browser_order("CHATGPT_BROWSER")


def _session_cache_valid(cookie_hash: str) -> bool:
    return _auth_instance._session_cache_valid(cookie_hash)

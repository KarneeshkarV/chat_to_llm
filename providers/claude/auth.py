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
    iter_zen_cookie_files,
    load_browser_cookie3,
    mask_token,
)

logger = logging.getLogger(__name__)

BROWSER_TOKEN_ALIASES = {"browser", "claude-browser", "cookies", "claude-cookies"}

_CLAUDE_DOMAINS = {
    "claude.ai",
    ".claude.ai",
    "api.anthropic.com",
    ".api.anthropic.com",
}
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_TRUE_VALUES = {"true", "1", "t", "y", "yes"}

_SESSION_CACHE: Dict[str, Any] = {}
_SESSION_CACHE_MULTI: Dict[str, Dict[str, Any]] = {}

_CLAUDE_BROWSER_AUTH = os.getenv("CLAUDE_BROWSER_AUTH", "false").lower() in _TRUE_VALUES
_CLAUDE_BROWSER_AUTH_ALLOW_REMOTE = (
    os.getenv("CLAUDE_BROWSER_AUTH_ALLOW_REMOTE", "false").lower() in _TRUE_VALUES
)
_CLAUDE_BASE_URL = os.getenv("CLAUDE_BASE_URL", "https://claude.ai").rstrip("/")
_CLAUDE_SESSION_TTL = 300


def _is_claude_domain(domain: str) -> bool:
    normalized = (domain or "").lower()
    if normalized in _CLAUDE_DOMAINS:
        return True
    return normalized.endswith(".claude.ai") or normalized.endswith(".api.anthropic.com")


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

    def ensure_browser_auth_request_allowed(self, request: Optional[Request] = None) -> None:
        if not _CLAUDE_BROWSER_AUTH:
            raise HTTPException(
                status_code=403,
                detail="Claude browser cookie auth is disabled. Set CLAUDE_BROWSER_AUTH=true to enable it.",
            )
        if _CLAUDE_BROWSER_AUTH_ALLOW_REMOTE or request is None:
            return
        host = request.client.host if request.client else ""
        if host not in _LOOPBACK_HOSTS:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Claude browser cookie auth is local-only by default. "
                    "Set CLAUDE_BROWSER_AUTH_ALLOW_REMOTE=true to allow remote callers."
                ),
            )

    def _cookie_header_from_env(self) -> Optional[str]:
        return os.getenv("CLAUDE_COOKIE", "").strip() or None

    def extract_cookie_header_from_browser(self) -> Tuple[Optional[str], List[str]]:
        env_cookie = self._cookie_header_from_env()
        if env_cookie:
            logger.info("Loaded Claude cookies from CLAUDE_COOKIE environment")
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

        for browser_name in get_browser_order("CLAUDE_BROWSER"):
            loader = browser_loaders.get(browser_name)
            if loader is None and browser_name != "zen":
                continue

            if browser_name == "firefox":
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "claude.ai"),
                        f"{browser_name}(default)",
                        _is_claude_domain,
                    )
                    if cookie_header:
                        return cookie_header, diagnostics
                except Exception as exc:
                    diagnostics.append(f"{browser_name}={type(exc).__name__}: {exc}")
                continue

            if browser_name == "zen":
                firefox_loader = browser_loaders.get("firefox")
                if firefox_loader is None:
                    continue
                zen_cookie_files = iter_zen_cookie_files("ZEN_PROFILE")
                if not zen_cookie_files:
                    try:
                        cookie_header = cookie_header_from_jar(
                            call_cookie_loader(firefox_loader, "claude.ai"),
                            "zen(default)",
                            _is_claude_domain,
                        )
                        if cookie_header:
                            return cookie_header, diagnostics
                    except Exception as exc:
                        diagnostics.append(f"zen={type(exc).__name__}: {exc}")
                    continue
                for cookie_file in zen_cookie_files:
                    profile = os.path.basename(os.path.dirname(cookie_file))
                    source = f"zen[{profile}]"
                    try:
                        cookie_header = cookie_header_from_jar(
                            call_cookie_loader(
                                firefox_loader, "claude.ai", cookie_file=cookie_file
                            ),
                            source,
                            _is_claude_domain,
                        )
                        if cookie_header:
                            return cookie_header, diagnostics
                    except Exception as exc:
                        diagnostics.append(f"{source}={type(exc).__name__}: {exc}")
                continue

            cookie_files = iter_chromium_cookie_files(browser_name, "CLAUDE_CHROME_PROFILE")
            if not cookie_files:
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "claude.ai"),
                        f"{browser_name}(default)",
                        _is_claude_domain,
                    )
                    if cookie_header:
                        return cookie_header, diagnostics
                except Exception as exc:
                    diagnostics.append(f"{browser_name}={type(exc).__name__}: {exc}")
                continue

            for cookie_file in cookie_files:
                profile = os.path.basename(os.path.dirname(cookie_file))
                if profile == "Network":
                    profile = os.path.basename(os.path.dirname(os.path.dirname(cookie_file)))
                source = f"{browser_name}[{profile}]"
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "claude.ai", cookie_file=cookie_file),
                        source,
                        _is_claude_domain,
                    )
                    if cookie_header:
                        return cookie_header, diagnostics
                except Exception as exc:
                    diagnostics.append(f"{source}={type(exc).__name__}: {exc}")

        return None, diagnostics

    def extract_all_cookie_headers(self) -> List[Tuple[str, str, str]]:
        env_cookie = self._cookie_header_from_env()
        if env_cookie:
            logger.info("Loaded Claude cookies from CLAUDE_COOKIE environment")
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

        for browser_name in get_browser_order("CLAUDE_BROWSER"):
            loader = browser_loaders.get(browser_name)
            if loader is None and browser_name != "zen":
                continue

            if browser_name == "firefox":
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "claude.ai"),
                        f"{browser_name}(default)",
                        _is_claude_domain,
                    )
                    if cookie_header:
                        results.append((f"{browser_name}[default]", cookie_header, browser_name))
                except Exception as exc:
                    logger.debug("firefox Claude cookie extraction failed: %s", exc)
                continue

            if browser_name == "zen":
                firefox_loader = browser_loaders.get("firefox")
                if firefox_loader is None:
                    continue
                zen_cookie_files = iter_zen_cookie_files("ZEN_PROFILE")
                if not zen_cookie_files:
                    try:
                        cookie_header = cookie_header_from_jar(
                            call_cookie_loader(firefox_loader, "claude.ai"),
                            "zen(default)",
                            _is_claude_domain,
                        )
                        if cookie_header:
                            results.append(("zen[default]", cookie_header, "zen(default)"))
                    except Exception as exc:
                        logger.debug("zen default Claude cookie extraction failed: %s", exc)
                    continue
                for cookie_file in zen_cookie_files:
                    profile = os.path.basename(os.path.dirname(cookie_file))
                    source = f"zen[{profile}]"
                    try:
                        cookie_header = cookie_header_from_jar(
                            call_cookie_loader(
                                firefox_loader, "claude.ai", cookie_file=cookie_file
                            ),
                            source,
                            _is_claude_domain,
                        )
                        if cookie_header:
                            results.append((source, cookie_header, source))
                    except Exception as exc:
                        logger.debug("%s Claude cookie extraction failed: %s", source, exc)
                continue

            cookie_files = iter_chromium_cookie_files(browser_name, "CLAUDE_CHROME_PROFILE")
            if not cookie_files:
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "claude.ai"),
                        f"{browser_name}(default)",
                        _is_claude_domain,
                    )
                    if cookie_header:
                        results.append((f"{browser_name}[default]", cookie_header, browser_name))
                except Exception as exc:
                    logger.debug(
                        "%s default Claude cookie extraction failed: %s", browser_name, exc
                    )
                continue

            for cookie_file in cookie_files:
                profile = os.path.basename(os.path.dirname(cookie_file))
                if profile == "Network":
                    profile = os.path.basename(os.path.dirname(os.path.dirname(cookie_file)))
                source = f"{browser_name}[{profile}]"
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "claude.ai", cookie_file=cookie_file),
                        source,
                        _is_claude_domain,
                    )
                    if cookie_header:
                        results.append((source, cookie_header, source))
                except Exception as exc:
                    logger.debug("%s Claude cookie extraction failed: %s", source, exc)

        return results

    async def validate_cookie_header(
        self, cookie_header: str, profile_key: str = "default"
    ) -> Dict[str, Any]:
        cookie_hash = hashlib.sha256(cookie_header.encode("utf-8")).hexdigest()
        client = Client(impersonate="chrome120")
        headers = {
            "accept": "application/json",
            "cookie": cookie_header,
            "origin": _CLAUDE_BASE_URL,
            "referer": f"{_CLAUDE_BASE_URL}/",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
        }
        try:
            bootstrap_response = await client.get(
                f"{_CLAUDE_BASE_URL}/api/bootstrap", headers=headers, timeout=20
            )
            bootstrap = await self._decode_json_response(
                bootstrap_response,
                profile_key=profile_key,
                invalid_detail="Claude browser cookies are expired or invalid (profile: %s).",
                generic_detail="Failed to fetch Claude bootstrap from browser cookies (profile: %s).",
            )

            account = bootstrap.get("account")
            if not account:
                raise HTTPException(
                    status_code=401,
                    detail="Claude bootstrap did not include an account (profile: %s)."
                    % profile_key,
                )

            org_response = await client.get(
                f"{_CLAUDE_BASE_URL}/api/organizations", headers=headers, timeout=20
            )
            organizations = await self._decode_json_response(
                org_response,
                profile_key=profile_key,
                invalid_detail="Claude browser cookies are expired or invalid (profile: %s).",
                generic_detail="Failed to fetch Claude organizations (profile: %s).",
            )
            org = self._select_chat_org(organizations)
            if not org:
                raise HTTPException(
                    status_code=401,
                    detail="No Claude organization with chat access was found (profile: %s)."
                    % profile_key,
                )

            user = {
                "email": account.get("email_address"),
                "uuid": account.get("uuid"),
                "organization": {
                    "uuid": org.get("uuid"),
                    "name": org.get("name"),
                    "capabilities": org.get("capabilities", []),
                },
            }
            result = {
                "profile_key": profile_key,
                "cookie_hash": cookie_hash,
                "cookie_header": cookie_header,
                "access_token_preview": mask_token(cookie_header),
                "expires_at": None,
                "user": user,
                "org_uuid": org.get("uuid"),
                "created_at": time.time(),
            }
            logger.info(
                "Loaded Claude browser session for profile '%s': %s",
                profile_key,
                mask_token(cookie_header),
            )
            return result
        finally:
            await client.close()

    async def _decode_json_response(
        self,
        response: Any,
        *,
        profile_key: str,
        invalid_detail: str,
        generic_detail: str,
    ) -> Dict[str, Any] | List[Any]:
        if response.status_code in (401, 403):
            raise HTTPException(status_code=401, detail=invalid_detail % profile_key)
        if response.status_code == 429:
            raise HTTPException(status_code=429, detail="rate-limit")
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code, detail=generic_detail % profile_key
            )

        try:
            return response.json()
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Claude response was not JSON (profile: %s): %s" % (profile_key, exc),
            )

    def _select_chat_org(self, organizations: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(organizations, list):
            return None
        candidates = []
        for org in organizations:
            capabilities = org.get("capabilities") or []
            if isinstance(capabilities, list) and "chat" in capabilities:
                candidates.append(org)
        if not candidates:
            return None
        return max(candidates, key=lambda item: len(item.get("capabilities") or []))

    async def get_all_sessions(self, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        all_cookies = self.extract_all_cookie_headers()
        if not all_cookies:
            raise HTTPException(
                status_code=401,
                detail="No Claude cookies found. Log in to claude.ai in a browser first.",
            )

        results: Dict[str, Dict[str, Any]] = {}
        for profile_key, cookie_header, _source in all_cookies:
            cookie_hash = hashlib.sha256(cookie_header.encode("utf-8")).hexdigest()
            if not force_refresh and self._session_cache_valid_multi(profile_key, cookie_hash):
                results[profile_key] = _SESSION_CACHE_MULTI[profile_key].copy()
                continue
            try:
                session = await self.validate_cookie_header(cookie_header, profile_key=profile_key)
                _SESSION_CACHE_MULTI[profile_key] = session.copy()
                results[profile_key] = session
            except Exception as exc:
                logger.warning("Skipping Claude profile '%s': %s", profile_key, exc)
                results[profile_key] = {"profile_key": profile_key, "error": str(exc)}
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
            detail = "No Claude cookies found. Log in to claude.ai in Arc/Chrome/Edge/Firefox/Brave first."
            if diagnostics:
                detail += " Diagnostics: " + "; ".join(diagnostics[:5])
            raise HTTPException(status_code=401, detail=detail)

        cookie_hash = hashlib.sha256(cookie_header.encode("utf-8")).hexdigest()
        if not force_refresh and self._session_cache_valid(cookie_hash):
            return _SESSION_CACHE.copy()

        session = await self.validate_cookie_header(cookie_header, profile_key="default")
        _SESSION_CACHE.clear()
        _SESSION_CACHE.update(session)
        return _SESSION_CACHE.copy()

    async def get_access_token(self, profile: Optional[str] = None) -> str:
        session = await self.get_browser_session(profile=profile)
        return session["cookie_header"]

    def _session_cache_valid(self, cookie_hash: str) -> bool:
        if not _SESSION_CACHE:
            return False
        if _SESSION_CACHE.get("cookie_hash") != cookie_hash:
            return False
        created_at = float(_SESSION_CACHE.get("created_at") or 0)
        return time.time() - created_at < _CLAUDE_SESSION_TTL

    def _session_cache_valid_multi(self, profile_key: str, cookie_hash: str) -> bool:
        cache = _SESSION_CACHE_MULTI.get(profile_key)
        if not cache:
            return False
        if cache.get("cookie_hash") != cookie_hash:
            return False
        created_at = float(cache.get("created_at") or 0)
        return time.time() - created_at < _CLAUDE_SESSION_TTL


_auth_instance = ClaudeAuth()

is_browser_auth_token = _auth_instance.is_browser_token
parse_browser_token = _auth_instance.parse_browser_token
ensure_browser_auth_request_allowed = _auth_instance.ensure_browser_auth_request_allowed
extract_cookie_header_from_browser = _auth_instance.extract_cookie_header_from_browser
extract_all_cookie_headers = _auth_instance.extract_all_cookie_headers
get_access_token_from_browser = _auth_instance.get_access_token
get_browser_session = _auth_instance.get_browser_session
get_all_browser_sessions = _auth_instance.get_all_sessions


def _get_browser_order() -> List[str]:
    return get_browser_order("CLAUDE_BROWSER")


def _session_cache_valid(cookie_hash: str) -> bool:
    return _auth_instance._session_cache_valid(cookie_hash)

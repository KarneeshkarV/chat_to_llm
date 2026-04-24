from __future__ import annotations

import logging
import os
import time
from http.cookies import SimpleCookie
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request

from providers.base import BaseAuth
from providers.common import (
    call_cookie_loader,
    cookie_header_from_jar,
    get_browser_order,
    iter_chromium_cookie_files,
    iter_zen_cookie_files,
    load_browser_cookie3,
    mask_token,
)

logger = logging.getLogger(__name__)

BROWSER_TOKEN_ALIASES = {"browser", "gemini-browser", "cookies", "gemini-cookies"}

_GEMINI_DOMAINS = {
    "google.com",
    ".google.com",
    "gemini.google.com",
    ".gemini.google.com",
}
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_TRUE_VALUES = {"true", "1", "t", "y", "yes"}
_SESSION_TTL = 300

_SESSION_CACHE: Dict[str, Dict[str, Any]] = {}
_SESSION_CACHE_MULTI: Dict[str, Dict[str, Any]] = {}

_GEMINI_BROWSER_AUTH = os.getenv("GEMINI_BROWSER_AUTH", "false").lower() in _TRUE_VALUES
_GEMINI_BROWSER_AUTH_ALLOW_REMOTE = (
    os.getenv("GEMINI_BROWSER_AUTH_ALLOW_REMOTE", "false").lower() in _TRUE_VALUES
)


def _is_gemini_domain(domain: str) -> bool:
    normalized = (domain or "").lower()
    if normalized in _GEMINI_DOMAINS:
        return True
    return normalized.endswith(".google.com")


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


def _cache_valid(entry: Optional[Dict[str, Any]]) -> bool:
    if not entry:
        return False
    cached_at = entry.get("cached_at", 0)
    return (time.time() - cached_at) < _SESSION_TTL


class GeminiAuth(BaseAuth):
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
        if not _GEMINI_BROWSER_AUTH:
            raise HTTPException(
                status_code=403,
                detail="Gemini browser cookie auth is disabled. Set GEMINI_BROWSER_AUTH=true to enable it.",
            )
        if _GEMINI_BROWSER_AUTH_ALLOW_REMOTE or request is None:
            return

        host = request.client.host if request.client else ""
        if host not in _LOOPBACK_HOSTS:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Gemini browser cookie auth is local-only by default. "
                    "Set GEMINI_BROWSER_AUTH_ALLOW_REMOTE=true to allow remote callers."
                ),
            )

    def _cookie_header_from_env(self) -> Optional[str]:
        raw_cookie = os.getenv("GEMINI_COOKIE", "").strip()
        if raw_cookie:
            return raw_cookie

        secure_1psid = os.getenv("GEMINI_SECURE_1PSID", "").strip()
        secure_1psidts = os.getenv("GEMINI_SECURE_1PSIDTS", "").strip()
        if not secure_1psid:
            return None

        cookie_parts = [f"__Secure-1PSID={secure_1psid}"]
        if secure_1psidts:
            cookie_parts.append(f"__Secure-1PSIDTS={secure_1psidts}")
        return "; ".join(cookie_parts)

    def extract_cookie_header_from_browser(self) -> Tuple[Optional[str], List[str]]:
        env_cookie = self._cookie_header_from_env()
        if env_cookie:
            logger.info("Loaded Gemini cookies from environment")
            return env_cookie, []

        browser_cookie3 = load_browser_cookie3()
        if browser_cookie3 is None:
            return None, ["browser-cookie3 is not installed"]

        browser_loaders = {
            "arc": getattr(browser_cookie3, "arc", None),
            "chrome": getattr(browser_cookie3, "chrome", None),
            "chromium": getattr(browser_cookie3, "chromium", None),
            "edge": getattr(browser_cookie3, "edge", None),
            "firefox": getattr(browser_cookie3, "firefox", None),
            "brave": getattr(browser_cookie3, "brave", None),
        }
        diagnostics: List[str] = []

        for browser_name in get_browser_order("GEMINI_BROWSER"):
            loader = browser_loaders.get(browser_name)
            if loader is None and browser_name != "zen":
                continue

            if browser_name == "firefox":
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "google.com"),
                        f"{browser_name}(default)",
                        _is_gemini_domain,
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
                            call_cookie_loader(firefox_loader, "google.com"),
                            "zen(default)",
                            _is_gemini_domain,
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
                                firefox_loader, "google.com", cookie_file=cookie_file
                            ),
                            source,
                            _is_gemini_domain,
                        )
                        if cookie_header:
                            return cookie_header, diagnostics
                    except Exception as exc:
                        diagnostics.append(f"{source}={type(exc).__name__}: {exc}")
                continue

            cookie_files = iter_chromium_cookie_files(browser_name, "GEMINI_CHROME_PROFILE")
            if not cookie_files:
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "google.com"),
                        f"{browser_name}(default)",
                        _is_gemini_domain,
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
                        call_cookie_loader(loader, "google.com", cookie_file=cookie_file),
                        source,
                        _is_gemini_domain,
                    )
                    if cookie_header:
                        return cookie_header, diagnostics
                except Exception as exc:
                    diagnostics.append(f"{source}={type(exc).__name__}: {exc}")

        return None, diagnostics

    def extract_all_cookie_headers(self) -> List[Tuple[str, str, str]]:
        env_cookie = self._cookie_header_from_env()
        if env_cookie:
            logger.info("Loaded Gemini cookies from environment")
            return [("env", env_cookie, "env")]

        browser_cookie3 = load_browser_cookie3()
        if browser_cookie3 is None:
            return []

        browser_loaders = {
            "arc": getattr(browser_cookie3, "arc", None),
            "chrome": getattr(browser_cookie3, "chrome", None),
            "chromium": getattr(browser_cookie3, "chromium", None),
            "edge": getattr(browser_cookie3, "edge", None),
            "firefox": getattr(browser_cookie3, "firefox", None),
            "brave": getattr(browser_cookie3, "brave", None),
        }
        results: List[Tuple[str, str, str]] = []

        for browser_name in get_browser_order("GEMINI_BROWSER"):
            loader = browser_loaders.get(browser_name)
            if loader is None and browser_name != "zen":
                continue

            if browser_name == "firefox":
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "google.com"),
                        f"{browser_name}(default)",
                        _is_gemini_domain,
                    )
                    if cookie_header:
                        results.append((f"{browser_name}[default]", cookie_header, browser_name))
                except Exception as exc:
                    logger.debug("firefox Gemini cookie extraction failed: %s", exc)
                continue

            if browser_name == "zen":
                firefox_loader = browser_loaders.get("firefox")
                if firefox_loader is None:
                    continue
                zen_cookie_files = iter_zen_cookie_files("ZEN_PROFILE")
                if not zen_cookie_files:
                    try:
                        cookie_header = cookie_header_from_jar(
                            call_cookie_loader(firefox_loader, "google.com"),
                            "zen(default)",
                            _is_gemini_domain,
                        )
                        if cookie_header:
                            results.append(("zen[default]", cookie_header, "zen(default)"))
                    except Exception as exc:
                        logger.debug("zen default Gemini cookie extraction failed: %s", exc)
                    continue
                for cookie_file in zen_cookie_files:
                    profile = os.path.basename(os.path.dirname(cookie_file))
                    source = f"zen[{profile}]"
                    try:
                        cookie_header = cookie_header_from_jar(
                            call_cookie_loader(
                                firefox_loader, "google.com", cookie_file=cookie_file
                            ),
                            source,
                            _is_gemini_domain,
                        )
                        if cookie_header:
                            results.append((source, cookie_header, source))
                    except Exception as exc:
                        logger.debug("%s Gemini cookie extraction failed: %s", source, exc)
                continue

            cookie_files = iter_chromium_cookie_files(browser_name, "GEMINI_CHROME_PROFILE")
            if not cookie_files:
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "google.com"),
                        f"{browser_name}(default)",
                        _is_gemini_domain,
                    )
                    if cookie_header:
                        results.append((f"{browser_name}[default]", cookie_header, browser_name))
                except Exception as exc:
                    logger.debug("%s default Gemini cookie extraction failed: %s", browser_name, exc)
                continue

            for cookie_file in cookie_files:
                profile = os.path.basename(os.path.dirname(cookie_file))
                if profile == "Network":
                    profile = os.path.basename(os.path.dirname(os.path.dirname(cookie_file)))
                source = f"{browser_name}[{profile}]"
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "google.com", cookie_file=cookie_file),
                        source,
                        _is_gemini_domain,
                    )
                    if cookie_header:
                        results.append((source, cookie_header, source))
                except Exception as exc:
                    logger.debug("%s Gemini cookie extraction failed: %s", source, exc)

        return results

    def _session_from_cookie_header(
        self, cookie_header: str, profile_key: str, source: str
    ) -> Dict[str, Any]:
        cookies = _parse_cookie_header(cookie_header)
        secure_1psid = cookies.get("__Secure-1PSID")
        if not secure_1psid:
            raise HTTPException(
                status_code=401,
                detail="Gemini cookies are missing __Secure-1PSID.",
            )

        session = {
            "profile": profile_key,
            "cookie_header": cookie_header,
            "secure_1psid": secure_1psid,
            "secure_1psidts": cookies.get("__Secure-1PSIDTS"),
            "access_token_preview": mask_token(secure_1psid),
            "expires_at": None,
            "user": {"source": source},
            "cached_at": time.time(),
        }
        return session

    async def get_access_token(self, profile: Optional[str] = None) -> str:
        session = await self.get_browser_session(profile=profile)
        return session["secure_1psid"]

    async def get_all_sessions(self, force_refresh: bool = False) -> Dict[str, Any]:
        if not force_refresh and _cache_valid(_SESSION_CACHE_MULTI.get("all")):
            return _SESSION_CACHE_MULTI["all"]["value"]

        sessions: Dict[str, Any] = {}
        for profile_key, cookie_header, source in self.extract_all_cookie_headers():
            try:
                sessions[profile_key] = self._session_from_cookie_header(
                    cookie_header,
                    profile_key=profile_key,
                    source=source,
                )
            except HTTPException as exc:
                sessions[profile_key] = {
                    "profile": profile_key,
                    "error": exc.detail,
                    "status": "error",
                }

        _SESSION_CACHE_MULTI["all"] = {"cached_at": time.time(), "value": sessions}
        return sessions

    async def get_browser_session(
        self, force_refresh: bool = False, profile: Optional[str] = None
    ) -> Dict[str, Any]:
        cache_key = profile or "default"
        cached = _SESSION_CACHE.get(cache_key)
        if not force_refresh and _cache_valid(cached):
            return cached

        if profile:
            all_headers = self.extract_all_cookie_headers()
            for profile_key, cookie_header, source in all_headers:
                if profile_key == profile:
                    session = self._session_from_cookie_header(
                        cookie_header,
                        profile_key=profile_key,
                        source=source,
                    )
                    _SESSION_CACHE[cache_key] = session
                    return session
            raise HTTPException(
                status_code=404,
                detail=f"Gemini browser profile not found: {profile}",
            )

        cookie_header, diagnostics = self.extract_cookie_header_from_browser()
        if not cookie_header:
            detail = "Unable to extract Gemini cookies from browser."
            if diagnostics:
                detail = "%s Diagnostics: %s" % (detail, "; ".join(diagnostics[:5]))
            raise HTTPException(status_code=401, detail=detail)

        session = self._session_from_cookie_header(
            cookie_header,
            profile_key=cache_key,
            source=profile or "default",
        )
        _SESSION_CACHE[cache_key] = session
        return session

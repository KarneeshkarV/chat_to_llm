from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

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
from providers.grok.browser_bootstrap import bootstrap_grok_session

logger = logging.getLogger(__name__)

BROWSER_TOKEN_ALIASES = {
    "browser",
    "grok-browser",
    "grok-cookies",
    "x-browser",
    "x-cookies",
    "cookies",
}

_GROK_DOMAINS = {
    "grok.com",
    ".grok.com",
    "x.ai",
    ".x.ai",
}
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

_TRUE_VALUES = {"true", "1", "t", "y", "yes"}

_GROK_BROWSER_AUTH = os.getenv("GROK_BROWSER_AUTH", "true").lower() in _TRUE_VALUES
_GROK_BROWSER_AUTH_ALLOW_REMOTE = (
    os.getenv("GROK_BROWSER_AUTH_ALLOW_REMOTE", "false").lower() in _TRUE_VALUES
)
_GROK_BROWSER_HEADLESS = os.getenv("GROK_BROWSER_HEADLESS", "true").lower() in _TRUE_VALUES
_GROK_BROWSER_EXECUTABLE = os.getenv("GROK_BROWSER_EXECUTABLE", "").strip() or None
_GROK_STATSIG_ID_OVERRIDE = os.getenv("GROK_X_STATSIG_ID", "").strip() or None

try:
    _GROK_SESSION_TTL = float(os.getenv("GROK_SESSION_TTL", "900"))
except ValueError:
    _GROK_SESSION_TTL = 900.0

try:
    _GROK_BOOTSTRAP_TIMEOUT = float(os.getenv("GROK_BOOTSTRAP_TIMEOUT", "30"))
except ValueError:
    _GROK_BOOTSTRAP_TIMEOUT = 30.0

# profile_key -> {cookie_header, statsig_id, cookie_hash, created_at, access_token_preview}
_SESSION_CACHE: Dict[str, Dict[str, Any]] = {}


def _is_grok_domain(domain: str) -> bool:
    normalized = (domain or "").lower()
    if normalized in _GROK_DOMAINS:
        return True
    return normalized.endswith(".grok.com") or normalized.endswith(".x.ai")


class GrokAuth(BaseAuth):
    def is_browser_token(self, token: Optional[str]) -> bool:
        if not token:
            return False
        lower = token.strip().lower()
        if lower in BROWSER_TOKEN_ALIASES:
            return True
        if ":" in lower:
            prefix = lower.split(":", 1)[0]
            return prefix in BROWSER_TOKEN_ALIASES
        return True

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
        return True, None

    def ensure_browser_auth_request_allowed(self, request: Any = None) -> None:
        if not _GROK_BROWSER_AUTH:
            raise HTTPException(
                status_code=403,
                detail="Grok browser auth is disabled. Set GROK_BROWSER_AUTH=true to enable it.",
            )
        if _GROK_BROWSER_AUTH_ALLOW_REMOTE or request is None:
            return
        host = request.client.host if request.client else ""
        if host not in _LOOPBACK_HOSTS:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Grok browser auth is local-only by default. "
                    "Set GROK_BROWSER_AUTH_ALLOW_REMOTE=true to allow remote callers."
                ),
            )

    def _cookie_header_from_env(self) -> Optional[str]:
        return os.getenv("GROK_COOKIE", "").strip() or None

    def extract_cookie_header_from_browser(self) -> Tuple[Optional[str], List[str]]:
        env_cookie = self._cookie_header_from_env()
        if env_cookie:
            logger.info("Loaded Grok cookies from GROK_COOKIE environment")
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

        for browser_name in get_browser_order("GROK_BROWSER"):
            loader = browser_loaders.get(browser_name)
            if loader is None and browser_name != "zen":
                continue

            if browser_name == "firefox":
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "grok.com"),
                        "%s(default)" % browser_name,
                        _is_grok_domain,
                    )
                    if cookie_header:
                        return cookie_header, diagnostics
                except Exception as exc:
                    diagnostics.append("%s=%s: %s" % (browser_name, type(exc).__name__, exc))
                continue

            if browser_name == "zen":
                firefox_loader = browser_loaders.get("firefox")
                if firefox_loader is None:
                    continue
                zen_cookie_files = iter_zen_cookie_files("ZEN_PROFILE")
                if not zen_cookie_files:
                    try:
                        cookie_header = cookie_header_from_jar(
                            call_cookie_loader(firefox_loader, "grok.com"),
                            "zen(default)",
                            _is_grok_domain,
                        )
                        if cookie_header:
                            return cookie_header, diagnostics
                    except Exception as exc:
                        diagnostics.append("zen=%s: %s" % (type(exc).__name__, exc))
                    continue
                for cookie_file in zen_cookie_files:
                    profile = os.path.basename(os.path.dirname(cookie_file))
                    source = "zen[%s]" % profile
                    try:
                        cookie_header = cookie_header_from_jar(
                            call_cookie_loader(firefox_loader, "grok.com", cookie_file=cookie_file),
                            source,
                            _is_grok_domain,
                        )
                        if cookie_header:
                            return cookie_header, diagnostics
                    except Exception as exc:
                        diagnostics.append("%s=%s: %s" % (source, type(exc).__name__, exc))
                continue

            cookie_files = iter_chromium_cookie_files(browser_name, "GROK_CHROME_PROFILE")
            if not cookie_files:
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "grok.com"),
                        "%s(default)" % browser_name,
                        _is_grok_domain,
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
                        call_cookie_loader(loader, "grok.com", cookie_file=cookie_file),
                        source,
                        _is_grok_domain,
                    )
                    if cookie_header:
                        return cookie_header, diagnostics
                except Exception as exc:
                    diagnostics.append("%s=%s: %s" % (source, type(exc).__name__, exc))

        return None, diagnostics

    def extract_all_cookie_headers(self) -> List[Tuple[str, str, str]]:
        env_cookie = self._cookie_header_from_env()
        if env_cookie:
            logger.info("Loaded Grok cookies from GROK_COOKIE environment")
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

        for browser_name in get_browser_order("GROK_BROWSER"):
            loader = browser_loaders.get(browser_name)
            if loader is None and browser_name != "zen":
                continue

            if browser_name == "firefox":
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "grok.com"),
                        "%s(default)" % browser_name,
                        _is_grok_domain,
                    )
                    if cookie_header:
                        results.append(
                            (
                                "%s[default]" % browser_name,
                                cookie_header,
                                "%s(default)" % browser_name,
                            )
                        )
                except Exception as exc:
                    logger.debug("firefox cookie extraction failed: %s" % exc)
                continue

            if browser_name == "zen":
                firefox_loader = browser_loaders.get("firefox")
                if firefox_loader is None:
                    continue
                zen_cookie_files = iter_zen_cookie_files("ZEN_PROFILE")
                if not zen_cookie_files:
                    try:
                        cookie_header = cookie_header_from_jar(
                            call_cookie_loader(firefox_loader, "grok.com"),
                            "zen(default)",
                            _is_grok_domain,
                        )
                        if cookie_header:
                            results.append(("zen[default]", cookie_header, "zen(default)"))
                    except Exception as exc:
                        logger.debug("zen default cookie extraction failed: %s" % exc)
                    continue
                for cookie_file in zen_cookie_files:
                    profile = os.path.basename(os.path.dirname(cookie_file))
                    source = "zen[%s]" % profile
                    try:
                        cookie_header = cookie_header_from_jar(
                            call_cookie_loader(firefox_loader, "grok.com", cookie_file=cookie_file),
                            source,
                            _is_grok_domain,
                        )
                        if cookie_header:
                            results.append((source, cookie_header, source))
                    except Exception as exc:
                        logger.debug("%s cookie extraction failed: %s" % (source, exc))
                continue

            cookie_files = iter_chromium_cookie_files(browser_name, "GROK_CHROME_PROFILE")
            if not cookie_files:
                try:
                    cookie_header = cookie_header_from_jar(
                        call_cookie_loader(loader, "grok.com"),
                        "%s(default)" % browser_name,
                        _is_grok_domain,
                    )
                    if cookie_header:
                        results.append(
                            (
                                "%s[default]" % browser_name,
                                cookie_header,
                                "%s(default)" % browser_name,
                            )
                        )
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
                        call_cookie_loader(loader, "grok.com", cookie_file=cookie_file),
                        source,
                        _is_grok_domain,
                    )
                    if cookie_header:
                        results.append((source, cookie_header, source))
                except Exception as exc:
                    logger.debug("%s cookie extraction failed: %s" % (source, exc))

        return results

    async def get_access_token(self, profile: Optional[str] = None) -> str:
        session = await self.get_browser_session(profile=profile)
        return session["cookie_header"]

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
            detail = "No Grok cookies found. Log in to grok.com in Arc/Chrome/Edge/Firefox/Brave/Zen first."
            if diagnostics:
                detail += " Diagnostics: " + "; ".join(diagnostics[:5])
            raise HTTPException(status_code=401, detail=detail)

        cookie_hash = hashlib.sha256(cookie_header.encode("utf-8")).hexdigest()
        cached = _SESSION_CACHE.get("default")
        if not force_refresh and self._session_entry_valid(cached, cookie_hash):
            return cached.copy()

        try:
            enriched_cookie, statsig_id = await self._bootstrap(cookie_header)
        except Exception as exc:
            raise HTTPException(
                status_code=401,
                detail="Failed to bootstrap Grok browser session: %s" % exc,
            )

        entry = {
            "profile_key": "default",
            "cookie_header": enriched_cookie,
            "statsig_id": statsig_id,
            "cookie_hash": cookie_hash,
            "created_at": time.time(),
            "access_token_preview": mask_token(enriched_cookie),
        }
        _SESSION_CACHE["default"] = entry
        return entry.copy()

    async def get_all_sessions(self, force_refresh: bool = False) -> Dict[str, Any]:
        all_cookies = self.extract_all_cookie_headers()
        if not all_cookies:
            raise HTTPException(
                status_code=401,
                detail="No Grok cookies found. Log in to grok.com in a browser first.",
            )

        results: Dict[str, Any] = {}
        for profile_key, cookie_header, source in all_cookies:
            cookie_hash = hashlib.sha256(cookie_header.encode("utf-8")).hexdigest()
            cached = _SESSION_CACHE.get(profile_key)
            if not force_refresh and self._session_entry_valid(cached, cookie_hash):
                results[profile_key] = cached.copy()
                continue
            try:
                enriched_cookie, statsig_id = await self._bootstrap(cookie_header)
                entry = {
                    "profile_key": profile_key,
                    "cookie_header": enriched_cookie,
                    "statsig_id": statsig_id,
                    "cookie_hash": cookie_hash,
                    "created_at": time.time(),
                    "access_token_preview": mask_token(enriched_cookie),
                    "source": source,
                }
                _SESSION_CACHE[profile_key] = entry
                results[profile_key] = entry
            except Exception as e:
                logger.warning("Bootstrap failed for profile '%s': %s", profile_key, e)
                results[profile_key] = {"profile_key": profile_key, "error": str(e)}

        return results

    async def _bootstrap(self, cookie_header: str) -> Tuple[str, str]:
        if _GROK_STATSIG_ID_OVERRIDE:
            logger.info("Using GROK_X_STATSIG_ID override, skipping browser bootstrap")
            return cookie_header, _GROK_STATSIG_ID_OVERRIDE
        proxy = os.getenv("GROK_PROXY", "").strip() or None
        return await bootstrap_grok_session(
            cookie_header,
            executable_path=_GROK_BROWSER_EXECUTABLE,
            proxy=proxy,
            headless=_GROK_BROWSER_HEADLESS,
            timeout=_GROK_BOOTSTRAP_TIMEOUT,
        )

    def _session_entry_valid(self, entry: Optional[Dict[str, Any]], cookie_hash: str) -> bool:
        if not entry:
            return False
        if entry.get("cookie_hash") != cookie_hash:
            return False
        created = entry.get("created_at", 0)
        if time.time() - created > _GROK_SESSION_TTL:
            return False
        return bool(entry.get("statsig_id"))

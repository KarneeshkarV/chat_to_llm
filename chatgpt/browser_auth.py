from __future__ import annotations

import base64
import glob
import hashlib
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request

from chatgpt.client import Client

logger = logging.getLogger(__name__)

BROWSER_TOKEN_ALIASES = {"browser", "chatgpt-browser", "cookies", "chatgpt-cookies"}

_CHATGPT_DOMAINS = {
    "chatgpt.com",
    ".chatgpt.com",
    "chat.openai.com",
    ".chat.openai.com",
}
_DEFAULT_BROWSER_ORDER = ["arc", "chrome", "edge", "firefox", "brave"]
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_ACCESS_TOKEN_CACHE: Dict[str, Any] = {}

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


def is_browser_auth_token(token: Optional[str]) -> bool:
    return bool(token and token.strip().lower() in BROWSER_TOKEN_ALIASES)


def ensure_browser_auth_request_allowed(request: Optional[Request] = None) -> None:
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


def _get_browser_order() -> List[str]:
    env_browser = os.getenv("CHATGPT_BROWSER", "").strip().lower()
    if not env_browser:
        return _DEFAULT_BROWSER_ORDER
    if env_browser not in set(_DEFAULT_BROWSER_ORDER):
        logger.warning("CHATGPT_BROWSER='%s' is invalid, using default order" % env_browser)
        return _DEFAULT_BROWSER_ORDER
    return [env_browser] + [b for b in _DEFAULT_BROWSER_ORDER if b != env_browser]


def _is_chatgpt_domain(domain: str) -> bool:
    normalized = (domain or "").lower()
    if normalized in _CHATGPT_DOMAINS:
        return True
    return normalized.endswith(".chatgpt.com") or normalized.endswith(".chat.openai.com")


def _profile_cookie_files(profile_dir: str) -> List[str]:
    candidates = [
        os.path.join(profile_dir, "Network", "Cookies"),
        os.path.join(profile_dir, "Cookies"),
    ]
    return [p for p in candidates if os.path.exists(p)]


def _chromium_roots(browser_name: str) -> List[str]:
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        mapping = {
            "chrome": [os.path.join(home, "Library", "Application Support", "Google", "Chrome")],
            "arc": [os.path.join(home, "Library", "Application Support", "Arc", "User Data")],
            "edge": [os.path.join(home, "Library", "Application Support", "Microsoft Edge")],
            "brave": [
                os.path.join(
                    home, "Library", "Application Support", "BraveSoftware", "Brave-Browser"
                )
            ],
        }
    elif sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        mapping = {
            "chrome": [os.path.join(local_appdata, "Google", "Chrome", "User Data")],
            "arc": [os.path.join(local_appdata, "Arc", "User Data")],
            "edge": [os.path.join(local_appdata, "Microsoft", "Edge", "User Data")],
            "brave": [os.path.join(local_appdata, "BraveSoftware", "Brave-Browser", "User Data")],
        }
    else:
        config_dir = os.path.join(home, ".config")
        mapping = {
            "chrome": [
                os.path.join(config_dir, "google-chrome"),
                os.path.join(config_dir, "Google", "Chrome"),
                os.path.join(config_dir, "chromium"),
            ],
            "arc": [],
            "edge": [os.path.join(config_dir, "microsoft-edge")],
            "brave": [os.path.join(config_dir, "BraveSoftware", "Brave-Browser")],
        }
    return [p for p in mapping.get(browser_name, []) if os.path.isdir(p)]


def _iter_chromium_cookie_files(browser_name: str) -> List[str]:
    paths: List[str] = []
    profile_name = os.getenv("CHATGPT_CHROME_PROFILE", "").strip()

    for root in _chromium_roots(browser_name):
        if profile_name:
            paths.extend(_profile_cookie_files(os.path.join(root, profile_name)))
            continue

        for default_profile in ("Default", "Guest Profile"):
            paths.extend(_profile_cookie_files(os.path.join(root, default_profile)))

        for profile_dir in sorted(glob.glob(os.path.join(root, "Profile *"))):
            paths.extend(_profile_cookie_files(profile_dir))

    return paths


def _load_browser_cookie3():
    try:
        import browser_cookie3

        return browser_cookie3
    except ImportError:
        return None


def _call_cookie_loader(loader, cookie_file: Optional[str] = None):
    kwargs: Dict[str, Any] = {"domain_name": "chatgpt.com"}
    if cookie_file:
        kwargs["cookie_file"] = cookie_file
    try:
        return loader(**kwargs)
    except TypeError:
        kwargs.pop("domain_name", None)
        return loader(**kwargs)


def _cookie_header_from_jar(jar: Any, source: str) -> Optional[str]:
    cookies: Dict[str, str] = {}
    matched_count = 0
    for cookie in jar:
        if not _is_chatgpt_domain(cookie.domain or ""):
            continue
        matched_count += 1
        if cookie.name and cookie.value:
            cookies[cookie.name] = cookie.value

    if not cookies:
        logger.debug("%s had no ChatGPT cookies (matched=%d)" % (source, matched_count))
        return None

    logger.info("Extracted %d ChatGPT cookies from %s" % (len(cookies), source))
    return "; ".join("%s=%s" % (name, value) for name, value in cookies.items())


def _cookie_header_from_env() -> Optional[str]:
    return (
        os.getenv("CHATGPT_COOKIE", "").strip()
        or os.getenv("CHATGPT_COOKIE_STRING", "").strip()
        or None
    )


def extract_cookie_header_from_browser() -> Tuple[Optional[str], List[str]]:
    env_cookie = _cookie_header_from_env()
    if env_cookie:
        logger.info("Loaded ChatGPT cookies from CHATGPT_COOKIE environment")
        return env_cookie, []

    browser_cookie3 = _load_browser_cookie3()
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

    for browser_name in _get_browser_order():
        loader = browser_loaders.get(browser_name)
        if loader is None:
            continue

        if browser_name == "firefox":
            try:
                cookie_header = _cookie_header_from_jar(
                    _call_cookie_loader(loader), "%s(default)" % browser_name
                )
                if cookie_header:
                    return cookie_header, diagnostics
            except Exception as exc:
                diagnostics.append("%s=%s: %s" % (browser_name, type(exc).__name__, exc))
            continue

        cookie_files = _iter_chromium_cookie_files(browser_name)
        if not cookie_files:
            try:
                cookie_header = _cookie_header_from_jar(
                    _call_cookie_loader(loader), "%s(default)" % browser_name
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
                cookie_header = _cookie_header_from_jar(
                    _call_cookie_loader(loader, cookie_file=cookie_file), source
                )
                if cookie_header:
                    return cookie_header, diagnostics
            except Exception as exc:
                diagnostics.append("%s=%s: %s" % (source, type(exc).__name__, exc))

    return None, diagnostics


def mask_token(token: str) -> str:
    if len(token) <= 16:
        return "***"
    return "%s...%s" % (token[:8], token[-6:])


def jwt_expires_at(access_token: str) -> Optional[int]:
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
        exp = data.get("exp")
        return int(exp) if exp else None
    except Exception:
        return None


def _session_cache_valid(cookie_hash: str) -> bool:
    if not _ACCESS_TOKEN_CACHE:
        return False
    if _ACCESS_TOKEN_CACHE.get("cookie_hash") != cookie_hash:
        return False
    expires_at = _ACCESS_TOKEN_CACHE.get("expires_at") or 0
    return int(time.time()) < int(expires_at) - 60


async def get_browser_session(force_refresh: bool = False) -> Dict[str, Any]:
    cookie_header, diagnostics = extract_cookie_header_from_browser()
    if not cookie_header:
        detail = "No ChatGPT cookies found. Log in to chatgpt.com in Arc/Chrome/Edge/Firefox/Brave first."
        if diagnostics:
            detail += " Diagnostics: " + "; ".join(diagnostics[:5])
        raise HTTPException(status_code=401, detail=detail)

    cookie_hash = hashlib.sha256(cookie_header.encode("utf-8")).hexdigest()
    if not force_refresh and _session_cache_valid(cookie_hash):
        return _ACCESS_TOKEN_CACHE.copy()

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
        response = await client.get("%s/api/auth/session" % host_url, headers=headers, timeout=15)
        if response.status_code in (401, 403):
            raise HTTPException(
                status_code=401, detail="ChatGPT browser cookies are expired or invalid."
            )
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail="Failed to fetch ChatGPT session from browser cookies.",
            )

        try:
            session = response.json()
        except Exception:
            raise HTTPException(status_code=502, detail="ChatGPT session response was not JSON.")

        access_token = session.get("accessToken")
        if not access_token:
            raise HTTPException(
                status_code=401, detail="ChatGPT session did not include an accessToken."
            )

        expires_at = jwt_expires_at(access_token) or int(time.time()) + 600
        _ACCESS_TOKEN_CACHE.clear()
        _ACCESS_TOKEN_CACHE.update(
            {
                "access_token": access_token,
                "access_token_preview": mask_token(access_token),
                "cookie_hash": cookie_hash,
                "expires_at": expires_at,
                "user": session.get("user"),
            }
        )
        logger.info(
            "Loaded ChatGPT access token from browser cookies: %s" % mask_token(access_token)
        )
        return _ACCESS_TOKEN_CACHE.copy()
    finally:
        await client.close()


async def get_access_token_from_browser(force_refresh: bool = False) -> str:
    session = await get_browser_session(force_refresh=force_refresh)
    return session["access_token"]

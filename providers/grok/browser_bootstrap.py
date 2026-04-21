from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


def _parse_cookie_header(cookie_header: str) -> List[dict]:
    cookies: List[dict] = []
    for part in cookie_header.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": ".grok.com",
                "path": "/",
                "secure": True,
            }
        )
    return cookies


def _cookies_to_header(cookies: List[dict]) -> str:
    parts: List[str] = []
    seen: set = set()
    for c in cookies:
        name = c.get("name")
        value = c.get("value")
        domain = (c.get("domain") or "").lower()
        if not name or value is None:
            continue
        if "grok.com" not in domain and "x.ai" not in domain:
            continue
        if name in seen:
            continue
        seen.add(name)
        parts.append("%s=%s" % (name, value))
    return "; ".join(parts)


def _resolve_executable(executable_path: Optional[str]) -> Optional[str]:
    if executable_path:
        return executable_path
    for candidate in ("/usr/bin/chromium", "/usr/bin/google-chrome", "/usr/bin/brave"):
        if os.path.exists(candidate):
            return candidate
    return None


async def bootstrap_grok_session(
    initial_cookie_header: str,
    *,
    executable_path: Optional[str] = None,
    proxy: Optional[str] = None,
    headless: bool = True,
    timeout: float = 30.0,
) -> Tuple[str, str]:
    """Launch a browser, seed cookies, capture x-statsig-id + refreshed cookie header.

    Returns ``(cookie_header, statsig_id)``. Raises ``RuntimeError`` on failure.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is required for Grok browser bootstrap. "
            "Install with: uv add playwright"
        ) from exc

    launch_kwargs: dict = {
        "headless": headless,
        "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    }
    resolved = _resolve_executable(executable_path)
    if resolved:
        launch_kwargs["executable_path"] = resolved
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs)
        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
                ),
            )
            cookies = _parse_cookie_header(initial_cookie_header)
            if cookies:
                await context.add_cookies(cookies)

            page = await context.new_page()

            captured: dict = {"statsig": None}

            def on_request(req):
                if captured["statsig"]:
                    return
                if "grok.com/rest/" not in req.url:
                    return
                try:
                    sid = req.headers.get("x-statsig-id")
                except Exception:
                    return
                if sid:
                    captured["statsig"] = sid

            page.on("request", on_request)

            try:
                await page.goto(
                    "https://grok.com/",
                    wait_until="domcontentloaded",
                    timeout=timeout * 1000,
                )
            except Exception as exc:
                raise RuntimeError("Failed to load grok.com: %s" % exc)

            try:
                await page.evaluate(
                    "fetch('/rest/app-chat/conversations?pageSize=1&responsesOnly=true',"
                    "{credentials:'include',headers:{'Accept':'application/json'}})"
                    ".catch(()=>null)"
                )
            except Exception as exc:
                logger.debug("trigger fetch evaluate failed: %s", exc)

            deadline = time.time() + timeout
            while time.time() < deadline:
                if captured["statsig"]:
                    break
                await asyncio.sleep(0.25)

            statsig_id = captured["statsig"]
            if not statsig_id:
                raise RuntimeError(
                    "Failed to capture x-statsig-id from browser session within %ss" % timeout
                )

            all_cookies = await context.cookies()
            cookie_header = _cookies_to_header(all_cookies) or initial_cookie_header
            logger.info(
                "Grok bootstrap captured statsig_id and %d grok/x.ai cookies",
                sum(
                    1
                    for c in all_cookies
                    if "grok.com" in (c.get("domain") or "") or "x.ai" in (c.get("domain") or "")
                ),
            )
            return cookie_header, statsig_id
        finally:
            await browser.close()

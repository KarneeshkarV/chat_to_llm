from __future__ import annotations

import html
import logging
import os
import re
import urllib.parse
from typing import Any, Dict

from providers.common.client import Client

logger = logging.getLogger(__name__)

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_PROXY_URL = os.getenv("PROXY_URL", "").strip() or None

_RESULT_BLOCK_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(value: str) -> str:
    return html.unescape(_TAG_RE.sub("", value)).strip()


def _decode_ddg_redirect(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        params = urllib.parse.parse_qs(parsed.query)
        if "uddg" in params and params["uddg"]:
            return urllib.parse.unquote(params["uddg"][0])
    return url


def parse_results(html_text: str, max_results: int) -> list[Dict[str, str]]:
    results: list[Dict[str, str]] = []
    for match in _RESULT_BLOCK_RE.finditer(html_text):
        raw_url, title_html, snippet_html = match.groups()
        results.append(
            {
                "title": _strip_tags(title_html),
                "url": _decode_ddg_redirect(raw_url),
                "snippet": _strip_tags(snippet_html),
            }
        )
        if len(results) >= max_results:
            break
    return results


def _truncate(results: list[Dict[str, str]], max_total_chars: int) -> list[Dict[str, str]]:
    out: list[Dict[str, str]] = []
    used = 0
    for item in results:
        budget = max_total_chars - used
        if budget <= 0:
            break
        snippet = item["snippet"]
        if len(snippet) > budget:
            snippet = snippet[: max(0, budget - 1)].rstrip() + "…"
        truncated = {"title": item["title"], "url": item["url"], "snippet": snippet}
        used += len(truncated["title"]) + len(truncated["url"]) + len(snippet)
        out.append(truncated)
    return out


async def web_search(
    query: str,
    max_results: int = 5,
    max_total_chars: int = 4000,
) -> Dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        return {"error": "query must be a non-empty string"}

    client = Client(proxy=_PROXY_URL, impersonate="chrome120", timeout=20)
    try:
        response = await client.post(
            _DDG_HTML_URL,
            data={"q": query.strip()},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        if response.status_code != 200:
            return {"error": "duckduckgo returned %s" % response.status_code}
        html_text = getattr(response, "text", None)
        if html_text is None:
            html_text = await response.atext()
    finally:
        await client.close()

    raw_results = parse_results(html_text, max_results=max_results)
    results = _truncate(raw_results, max_total_chars=max_total_chars)
    return {"query": query.strip(), "results": results}

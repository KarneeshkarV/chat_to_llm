from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

import pybase64

logger = logging.getLogger(__name__)

_cores = [8, 16, 24, 32]
_time_layout = "%a %b %d %Y %H:%M:%S"

_cached_scripts: List[str] = []
_cached_dpl: Optional[str] = None
_cached_dpl_time: int = 0

_pow_config_cache: Dict[str, Tuple[Any, float]] = {}
_POW_CONFIG_TTL = 7 * 24 * 3600

_navigator_key = [
    "registerProtocolHandler−function registerProtocolHandler() { [native code] }",
    "storage−[object StorageManager]",
    "locks−[object LockManager]",
    "appCodeName−Mozilla",
    "permissions−[object Permissions]",
    "share−function share() { [native code] }",
    "webdriver−false",
    "managed−[object NavigatorManagedData]",
    "canShare−function canShare() { [native code] }",
    "vendor−Google Inc.",
    "mediaDevices−[object MediaDevices]",
    "vibrate−function vibrate() { [native code] }",
    "storageBuckets−[object StorageBucketManager]",
    "mediaCapabilities−[object MediaCapabilities]",
    "getGamepads−function getGamepads() { [native code] }",
    "bluetooth−[object Bluetooth]",
    "cookieEnabled−true",
    "virtualKeyboard−[object VirtualKeyboard]",
    "product−Gecko",
    "xr−[object XRSystem]",
    "clipboard−[object Clipboard]",
    "productSub−20030107",
    "login−[object NavigatorLogin]",
    "vendorSub−",
    "onLine−true",
    "mimeTypes−[object MimeTypeArray]",
    "credentials−[object CredentialsContainer]",
    "presentation−[object Presentation]",
    "serviceWorker−[object ServiceWorkerContainer]",
    "gpu−[object GPU]",
    "pdfViewerEnabled−true",
    "language−en-US",
    "geolocation−[object Geolocation]",
    "userAgentData−[object NavigatorUAData]",
    "hardwareConcurrency−32",
    "scheduling−[object Scheduling]",
]

_document_key = ["_reactListeningo743lnnpvdg", "location"]

_window_key = [
    "0",
    "window",
    "self",
    "document",
    "name",
    "location",
    "customElements",
    "history",
    "navigation",
    "locationbar",
    "menubar",
    "personalbar",
    "scrollbars",
    "statusbar",
    "toolbar",
    "status",
    "closed",
    "frames",
    "length",
    "top",
    "opener",
    "parent",
    "frameElement",
    "navigator",
    "origin",
    "external",
    "screen",
    "innerWidth",
    "innerHeight",
    "scrollX",
    "pageXOffset",
    "scrollY",
    "pageYOffset",
    "visualViewport",
    "screenX",
    "screenY",
    "outerWidth",
    "outerHeight",
    "devicePixelRatio",
    "clientInformation",
    "screenLeft",
    "screenTop",
    "styleMedia",
    "isSecureContext",
    "trustedTypes",
    "performance",
    "crypto",
    "indexedDB",
    "sessionStorage",
    "localStorage",
    "caches",
    "cookieStore",
    "scheduler",
    "speechSynthesis",
]


class _ScriptSrcParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: List[str] = []
        self.dpl: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag == "script":
            attrs_dict = dict(attrs)
            if "src" in attrs_dict:
                src = attrs_dict["src"]
                self.scripts.append(src)
                match = re.search(r"c/[^/]*/_", src)
                if match:
                    self.dpl = match.group(0)


def _get_data_build_from_html(html_content: str) -> None:
    global _cached_scripts, _cached_dpl, _cached_dpl_time
    parser = _ScriptSrcParser()
    parser.feed(html_content)
    if parser.scripts:
        _cached_scripts = parser.scripts
    if not _cached_scripts:
        _cached_scripts.append("https://chatgpt.com/backend-api/sentinel/sdk.js")
    if parser.dpl:
        _cached_dpl = parser.dpl
        _cached_dpl_time = int(time.time())
        logger.info("Found dpl from script src: %s" % _cached_dpl)
    if not _cached_dpl:
        match = re.search(r'<html[^>]*data-build="([^"]*)"', html_content)
        if match:
            _cached_dpl = match.group(1)
            _cached_dpl_time = int(time.time())
            logger.info("Found dpl from data-build: %s" % _cached_dpl)


async def get_dpl(service: Any) -> bool:
    global _cached_scripts, _cached_dpl, _cached_dpl_time
    if _cached_dpl and int(time.time()) - _cached_dpl_time < 15 * 60:
        return True
    headers = service.base_headers.copy()
    _cached_scripts = []
    _cached_dpl = None
    try:
        r = await service.s.get(f"{service.host_url}/", headers=headers, timeout=5)
        r.raise_for_status()
        _get_data_build_from_html(r.text)
        if not _cached_dpl:
            raise Exception("No DPL found in ChatGPT HTML")
        return True
    except Exception as e:
        logger.info("Failed to get dpl: %s" % e)
        _cached_dpl = None
        _cached_dpl_time = int(time.time())
        return False


def _parse_time() -> str:
    now = datetime.now(timezone(timedelta(hours=-5)))
    return now.strftime(_time_layout) + " GMT-0500 (Eastern Standard Time)"


def _cache_key(user_agent: str, req_token: Optional[str] = None) -> str:
    return hashlib.md5((user_agent + (req_token or "")).encode()).hexdigest()


def get_config(user_agent: str, req_token: Optional[str] = None) -> list:
    key = _cache_key(user_agent, req_token)
    now = time.time()
    if key in _pow_config_cache:
        cached_val, cached_time = _pow_config_cache[key]
        if now - cached_time < _POW_CONFIG_TTL:
            return cached_val

    config = [
        random.choice([1920 + 1080, 2560 + 1440, 1920 + 1200, 2560 + 1600]),
        _parse_time(),
        4294705152,
        0,
        user_agent,
        random.choice(_cached_scripts) if _cached_scripts else "",
        _cached_dpl or "",
        "en-US",
        "en-US,es-US,en,es",
        0,
        random.choice(_navigator_key),
        random.choice(_document_key),
        random.choice(_window_key),
        time.perf_counter() * 1000,
        str(uuid.uuid4()),
        "",
        random.choice(_cores),
        time.time() * 1000 - (time.perf_counter() * 1000),
    ]
    _pow_config_cache[key] = (config, now)
    return config


def _generate_answer(seed: str, diff: str, config: list) -> Tuple[str, bool]:
    diff_len = len(diff)
    seed_encoded = seed.encode()
    static_part1 = (
        json.dumps(config[:3], separators=(",", ":"), ensure_ascii=False)[:-1] + ","
    ).encode()
    static_part2 = (
        "," + json.dumps(config[4:9], separators=(",", ":"), ensure_ascii=False)[1:-1] + ","
    ).encode()
    static_part3 = (
        "," + json.dumps(config[10:], separators=(",", ":"), ensure_ascii=False)[1:]
    ).encode()

    target_diff = bytes.fromhex(diff)

    for i in range(500000):
        dyn_i = str(i).encode()
        dyn_j = str(i >> 1).encode()
        final_bytes = static_part1 + dyn_i + static_part2 + dyn_j + static_part3
        base_encode = pybase64.b64encode(final_bytes)
        hash_value = hashlib.sha3_512(seed_encoded + base_encode).digest()
        if hash_value[:diff_len] <= target_diff:
            return base_encode.decode(), True

    fallback = pybase64.b64encode(b'"' + seed.encode() + b'"').decode()
    return "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D" + fallback, False


def get_answer_token(seed: str, diff: str, config: list) -> Tuple[str, bool]:
    start = time.time()
    answer, solved = _generate_answer(seed, diff, config)
    end = time.time()
    logger.info("diff: %s, time: %.1fms, solved: %s" % (diff, (end - start) * 1000, solved))
    return "gAAAAAB" + answer, solved


def get_requirements_token(config: list) -> str:
    require, _solved = _generate_answer(format(random.random()), "0fffff", config)
    return "gAAAAAC" + require

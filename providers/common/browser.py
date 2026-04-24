from __future__ import annotations

import glob
import logging
import os
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_BROWSER_ORDER = ["arc", "chrome", "chromium", "edge", "firefox", "brave", "zen"]


def get_browser_order(env_var_name: str = "BROWSER") -> List[str]:
    env_browser = os.getenv(env_var_name, "").strip().lower()
    if not env_browser:
        return _DEFAULT_BROWSER_ORDER
    if env_browser not in set(_DEFAULT_BROWSER_ORDER):
        logger.warning("%s='%s' is invalid, using default order" % (env_var_name, env_browser))
        return _DEFAULT_BROWSER_ORDER
    return [env_browser] + [b for b in _DEFAULT_BROWSER_ORDER if b != env_browser]


def profile_cookie_files(profile_dir: str) -> List[str]:
    candidates = [
        os.path.join(profile_dir, "Network", "Cookies"),
        os.path.join(profile_dir, "Cookies"),
    ]
    return [p for p in candidates if os.path.exists(p)]


def chromium_roots(browser_name: str) -> List[str]:
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        mapping = {
            "chrome": [os.path.join(home, "Library", "Application Support", "Google", "Chrome")],
            "chromium": [os.path.join(home, "Library", "Application Support", "Chromium")],
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
            "chromium": [os.path.join(local_appdata, "Chromium", "User Data")],
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
            ],
            "chromium": [os.path.join(config_dir, "chromium")],
            "arc": [],
            "edge": [os.path.join(config_dir, "microsoft-edge")],
            "brave": [os.path.join(config_dir, "BraveSoftware", "Brave-Browser")],
        }
    return [p for p in mapping.get(browser_name, []) if os.path.isdir(p)]


def zen_roots() -> List[str]:
    """Return possible Zen Browser profile root directories."""
    home = os.path.expanduser("~")
    candidates: List[str] = []
    if sys.platform == "darwin":
        candidates.append(os.path.join(home, "Library", "Application Support", "Zen"))
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        candidates.append(os.path.join(appdata, "Zen"))
    else:
        candidates.append(os.path.join(home, ".zen"))
        # Flatpak
        candidates.append(os.path.join(home, ".var", "app", "io.github.zen_browser.zen", ".zen"))
    return [p for p in candidates if os.path.isdir(p)]


def iter_zen_cookie_files(profile_env_var: str = "ZEN_PROFILE") -> List[str]:
    """Iterate Zen Browser profiles and return paths to cookies.sqlite files."""
    paths: List[str] = []
    profile_name = os.getenv(profile_env_var, "").strip()

    for root in zen_roots():
        if profile_name:
            cookie_path = os.path.join(root, profile_name, "cookies.sqlite")
            if os.path.exists(cookie_path):
                paths.append(cookie_path)
            continue

        # Look for default profiles
        for entry in sorted(os.listdir(root)):
            entry_path = os.path.join(root, entry)
            if not os.path.isdir(entry_path):
                continue
            if "default" in entry.lower():
                cookie_path = os.path.join(entry_path, "cookies.sqlite")
                if os.path.exists(cookie_path):
                    paths.append(cookie_path)

    return paths


def iter_chromium_cookie_files(
    browser_name: str, profile_env_var: str = "CHROME_PROFILE"
) -> List[str]:
    paths: List[str] = []
    profile_name = os.getenv(profile_env_var, "").strip()

    for root in chromium_roots(browser_name):
        if profile_name:
            paths.extend(profile_cookie_files(os.path.join(root, profile_name)))
            continue

        for default_profile in ("Default", "Guest Profile"):
            paths.extend(profile_cookie_files(os.path.join(root, default_profile)))

        for profile_dir in sorted(glob.glob(os.path.join(root, "Profile *"))):
            paths.extend(profile_cookie_files(profile_dir))

    return paths


def load_browser_cookie3():
    try:
        import browser_cookie3

        return browser_cookie3
    except ImportError:
        return None


def call_cookie_loader(loader, domain_name: str, cookie_file: Optional[str] = None):
    kwargs: Dict[str, Any] = {"domain_name": domain_name}
    if cookie_file:
        kwargs["cookie_file"] = cookie_file
    try:
        return loader(**kwargs)
    except TypeError:
        kwargs.pop("domain_name", None)
        return loader(**kwargs)


def cookie_header_from_jar(jar: Any, source: str, domain_filter) -> Optional[str]:
    cookies: Dict[str, str] = {}
    matched_count = 0
    for cookie in jar:
        if not domain_filter(cookie.domain or ""):
            continue
        matched_count += 1
        if cookie.name and cookie.value:
            cookies[cookie.name] = cookie.value

    if not cookies:
        logger.debug("%s had no matching cookies (matched=%d)" % (source, matched_count))
        return None

    logger.info("Extracted %d cookies from %s" % (len(cookies), source))
    return "; ".join("%s=%s" % (name, value) for name, value in cookies.items())

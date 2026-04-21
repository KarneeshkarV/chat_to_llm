from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import providers.gemini.auth as gemini_auth_module
from providers.gemini.auth import BROWSER_TOKEN_ALIASES, GeminiAuth


class TestGeminiAuth:
    def test_is_browser_token_with_aliases(self):
        auth = GeminiAuth()
        for alias in BROWSER_TOKEN_ALIASES:
            assert auth.is_browser_token(alias) is True

    def test_parse_browser_token_with_profile(self):
        auth = GeminiAuth()
        assert auth.parse_browser_token("gemini-browser:default") == (True, "default")

    def test_cookie_header_from_env_prefers_raw_cookie(self, monkeypatch):
        monkeypatch.setenv("GEMINI_COOKIE", "__Secure-1PSID=abc; __Secure-1PSIDTS=def")
        monkeypatch.setenv("GEMINI_SECURE_1PSID", "ignored")
        auth = GeminiAuth()
        assert auth._cookie_header_from_env() == "__Secure-1PSID=abc; __Secure-1PSIDTS=def"

    def test_cookie_header_from_env_builds_from_direct_values(self, monkeypatch):
        monkeypatch.delenv("GEMINI_COOKIE", raising=False)
        monkeypatch.setenv("GEMINI_SECURE_1PSID", "abc")
        monkeypatch.setenv("GEMINI_SECURE_1PSIDTS", "def")
        auth = GeminiAuth()
        assert auth._cookie_header_from_env() == "__Secure-1PSID=abc; __Secure-1PSIDTS=def"

    def test_get_browser_session_uses_selected_profile(self, monkeypatch):
        auth = GeminiAuth()
        monkeypatch.setattr(
            auth,
            "extract_all_cookie_headers",
            lambda: [("brave[Default]", "__Secure-1PSID=abc; __Secure-1PSIDTS=def", "brave")],
        )

        session = asyncio.run(auth.get_browser_session(profile="brave[Default]"))
        assert session["secure_1psid"] == "abc"
        assert session["secure_1psidts"] == "def"

    def test_ensure_browser_auth_request_allowed_blocks_remote(self, monkeypatch):
        auth = GeminiAuth()
        monkeypatch.setattr(gemini_auth_module, "_GEMINI_BROWSER_AUTH", True)
        monkeypatch.setattr(gemini_auth_module, "_GEMINI_BROWSER_AUTH_ALLOW_REMOTE", False)
        request = SimpleNamespace(headers={}, client=SimpleNamespace(host="192.168.1.2"))

        with pytest.raises(HTTPException) as exc_info:
            auth.ensure_browser_auth_request_allowed(request)
        assert exc_info.value.status_code == 403

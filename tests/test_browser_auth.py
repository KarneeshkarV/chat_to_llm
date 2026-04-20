from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace
import pytest

from chatgpt import browser_auth


class TestBrowserAliasDetection:
    @pytest.mark.parametrize(
        "token", ["browser", "Browser", "BROWSER", "chatgpt-browser", "cookies", "chatgpt-cookies"]
    )
    def test_aliases_match(self, token):
        assert browser_auth.is_browser_auth_token(token) is True

    @pytest.mark.parametrize("token", ["my-token", "eyJhbGciOi", "abc123", "", None])
    def test_non_aliases_dont_match(self, token):
        assert browser_auth.is_browser_auth_token(token) is False


class TestDisabledBrowserAuth:
    def test_returns_403_when_disabled(self, monkeypatch):
        monkeypatch.setattr(browser_auth, "_CHATGPT_BROWSER_AUTH", False)
        with pytest.raises(Exception) as exc_info:
            browser_auth.ensure_browser_auth_request_allowed()
        assert exc_info.value.status_code == 403
        assert "disabled" in exc_info.value.detail.lower()

    def test_allows_when_enabled(self, monkeypatch):
        monkeypatch.setattr(browser_auth, "_CHATGPT_BROWSER_AUTH", True)
        monkeypatch.setattr(browser_auth, "_CHATGPT_BROWSER_AUTH_ALLOW_REMOTE", True)
        browser_auth.ensure_browser_auth_request_allowed()


class TestLocalhostOnlyGuard:
    def test_remote_caller_blocked(self, monkeypatch):
        monkeypatch.setattr(browser_auth, "_CHATGPT_BROWSER_AUTH", True)
        monkeypatch.setattr(browser_auth, "_CHATGPT_BROWSER_AUTH_ALLOW_REMOTE", False)
        request = SimpleNamespace(client=SimpleNamespace(host="192.168.1.100"))
        with pytest.raises(Exception) as exc_info:
            browser_auth.ensure_browser_auth_request_allowed(request)
        assert exc_info.value.status_code == 403
        assert "local-only" in exc_info.value.detail

    @pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
    def test_localhost_allowed(self, monkeypatch, host):
        monkeypatch.setattr(browser_auth, "_CHATGPT_BROWSER_AUTH", True)
        monkeypatch.setattr(browser_auth, "_CHATGPT_BROWSER_AUTH_ALLOW_REMOTE", False)
        request = SimpleNamespace(client=SimpleNamespace(host=host))
        browser_auth.ensure_browser_auth_request_allowed(request)

    def test_remote_allowed_when_flag_set(self, monkeypatch):
        monkeypatch.setattr(browser_auth, "_CHATGPT_BROWSER_AUTH", True)
        monkeypatch.setattr(browser_auth, "_CHATGPT_BROWSER_AUTH_ALLOW_REMOTE", True)
        request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.1"))
        browser_auth.ensure_browser_auth_request_allowed(request)


class TestTokenMasking:
    def test_short_token(self):
        assert browser_auth.mask_token("short") == "***"

    def test_exactly_16_chars(self):
        assert browser_auth.mask_token("x" * 16) == "***"

    def test_long_token(self):
        token = "a" * 8 + "MIDDLE" + "b" * 6
        result = browser_auth.mask_token(token)
        assert result.startswith("aaaaaaaa")
        assert result.endswith("bbbbbb")
        assert "..." in result

    def test_17_char_token(self):
        token = "a" * 17
        result = browser_auth.mask_token(token)
        assert result != "***"
        assert "..." in result


class TestJWTExpiryParsing:
    def _make_jwt(self, exp: int) -> str:
        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).rstrip(b"=").decode()
        )
        payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
        return "%s.%s.signature" % (header, payload)

    def test_valid_jwt(self):
        exp = int(time.time()) + 3600
        token = self._make_jwt(exp)
        assert browser_auth.jwt_expires_at(token) == exp

    def test_no_exp_claim(self):
        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).rstrip(b"=").decode()
        )
        payload = (
            base64.urlsafe_b64encode(json.dumps({"sub": "test"}).encode()).rstrip(b"=").decode()
        )
        token = "%s.%s.sig" % (header, payload)
        assert browser_auth.jwt_expires_at(token) is None

    def test_invalid_jwt(self):
        assert browser_auth.jwt_expires_at("not-a-jwt") is None

    def test_empty_string(self):
        assert browser_auth.jwt_expires_at("") is None


class TestCacheValidity:
    def test_cache_valid_with_future_expiry(self, monkeypatch):
        monkeypatch.setattr(
            browser_auth,
            "_ACCESS_TOKEN_CACHE",
            {
                "cookie_hash": "abc",
                "expires_at": int(time.time()) + 3600,
            },
        )
        assert browser_auth._session_cache_valid("abc") is True

    def test_cache_invalid_with_past_expiry(self, monkeypatch):
        monkeypatch.setattr(
            browser_auth,
            "_ACCESS_TOKEN_CACHE",
            {
                "cookie_hash": "abc",
                "expires_at": int(time.time()) - 100,
            },
        )
        assert browser_auth._session_cache_valid("abc") is False

    def test_cache_invalid_with_different_hash(self, monkeypatch):
        monkeypatch.setattr(
            browser_auth,
            "_ACCESS_TOKEN_CACHE",
            {
                "cookie_hash": "abc",
                "expires_at": int(time.time()) + 3600,
            },
        )
        assert browser_auth._session_cache_valid("different") is False

    def test_empty_cache_invalid(self, monkeypatch):
        monkeypatch.setattr(browser_auth, "_ACCESS_TOKEN_CACHE", {})
        assert browser_auth._session_cache_valid("abc") is False


class TestEnvCookieOverride:
    def test_env_cookie_takes_priority(self, monkeypatch):
        monkeypatch.setenv("CHATGPT_COOKIE", "test=abc; session=xyz")
        result, diagnostics = browser_auth.extract_cookie_header_from_browser()
        assert result == "test=abc; session=xyz"
        assert diagnostics == []

    def test_env_cookie_string_alt(self, monkeypatch):
        monkeypatch.delenv("CHATGPT_COOKIE", raising=False)
        monkeypatch.setenv("CHATGPT_COOKIE_STRING", "alt=123")
        result, diagnostics = browser_auth.extract_cookie_header_from_browser()
        assert result == "alt=123"

    def test_no_env_no_browser_cookie3(self, monkeypatch):
        monkeypatch.delenv("CHATGPT_COOKIE", raising=False)
        monkeypatch.delenv("CHATGPT_COOKIE_STRING", raising=False)
        monkeypatch.setattr(browser_auth, "_load_browser_cookie3", lambda: None)
        result, diagnostics = browser_auth.extract_cookie_header_from_browser()
        assert result is None
        assert "browser-cookie3" in diagnostics[0]


class TestBrowserOrder:
    def test_default_order(self, monkeypatch):
        monkeypatch.delenv("CHATGPT_BROWSER", raising=False)
        order = browser_auth._get_browser_order()
        assert order == ["arc", "chrome", "edge", "firefox", "brave"]

    def test_env_overrides_priority(self, monkeypatch):
        monkeypatch.setenv("CHATGPT_BROWSER", "firefox")
        order = browser_auth._get_browser_order()
        assert order[0] == "firefox"
        assert len(order) == 5

    def test_invalid_env_uses_default(self, monkeypatch):
        monkeypatch.setenv("CHATGPT_BROWSER", "safari")
        order = browser_auth._get_browser_order()
        assert order == ["arc", "chrome", "edge", "firefox", "brave"]


class TestChatGPTDomainMatching:
    @pytest.mark.parametrize(
        "domain",
        [
            "chatgpt.com",
            ".chatgpt.com",
            "chat.openai.com",
            ".chat.openai.com",
            "sub.chatgpt.com",
            "sub.chat.openai.com",
        ],
    )
    def test_matching_domains(self, domain):
        assert browser_auth._is_chatgpt_domain(domain) is True

    @pytest.mark.parametrize("domain", ["example.com", "openai.com", ".google.com", ""])
    def test_non_matching_domains(self, domain):
        assert browser_auth._is_chatgpt_domain(domain) is False

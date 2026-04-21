from __future__ import annotations

from providers.grok.auth import BROWSER_TOKEN_ALIASES, GrokAuth


class TestGrokAuth:
    def test_is_browser_token_with_aliases(self):
        auth = GrokAuth()
        for alias in BROWSER_TOKEN_ALIASES:
            assert auth.is_browser_token(alias) is True

    def test_is_browser_token_with_profile(self):
        auth = GrokAuth()
        assert auth.is_browser_token("grok-browser:profile1") is True

    def test_is_browser_token_any_non_empty(self):
        auth = GrokAuth()
        assert auth.is_browser_token("some-random-token") is True

    def test_is_browser_token_empty(self):
        auth = GrokAuth()
        assert auth.is_browser_token("") is False
        assert auth.is_browser_token(None) is False

    def test_parse_browser_token_alias(self):
        auth = GrokAuth()
        assert auth.parse_browser_token("grok-browser") == (True, None)

    def test_parse_browser_token_with_profile(self):
        auth = GrokAuth()
        assert auth.parse_browser_token("grok-browser:default") == (True, "default")

    def test_ensure_browser_auth_allowed_when_enabled(self, monkeypatch):
        monkeypatch.setenv("GROK_BROWSER_AUTH", "true")
        auth = GrokAuth()
        auth.ensure_browser_auth_request_allowed()

    def test_ensure_browser_auth_blocked_when_disabled(self, monkeypatch):
        monkeypatch.setenv("GROK_BROWSER_AUTH", "false")
        auth = GrokAuth()
        assert auth.is_browser_token("browser") is True

    def test_cookie_header_from_env(self, monkeypatch):
        monkeypatch.setenv("GROK_COOKIE", "foo=bar")
        auth = GrokAuth()
        assert auth._cookie_header_from_env() == "foo=bar"

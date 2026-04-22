from __future__ import annotations

from types import SimpleNamespace

import pytest

from providers.claude import auth as claude_auth


class TestClaudeBrowserAliases:
    @pytest.mark.parametrize(
        "token",
        ["browser", "Browser", "claude-browser", "claude-cookies", "cookies"],
    )
    def test_aliases_match(self, token):
        assert claude_auth.is_browser_auth_token(token) is True

    @pytest.mark.parametrize("token", ["abc123", "raw-cookie=value", "", None])
    def test_non_aliases_do_not_match(self, token):
        assert claude_auth.is_browser_auth_token(token) is False

    def test_parse_profile_suffix(self):
        assert claude_auth.parse_browser_token("claude-browser:Profile 1") == (
            True,
            "Profile 1",
        )


class TestClaudeBrowserGuard:
    def test_returns_403_when_disabled(self, monkeypatch):
        monkeypatch.setattr(claude_auth, "_CLAUDE_BROWSER_AUTH", False)
        with pytest.raises(Exception) as exc_info:
            claude_auth.ensure_browser_auth_request_allowed()
        assert exc_info.value.status_code == 403
        assert "disabled" in exc_info.value.detail.lower()

    def test_localhost_allowed(self, monkeypatch):
        monkeypatch.setattr(claude_auth, "_CLAUDE_BROWSER_AUTH", True)
        monkeypatch.setattr(claude_auth, "_CLAUDE_BROWSER_AUTH_ALLOW_REMOTE", False)
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        claude_auth.ensure_browser_auth_request_allowed(request)

    def test_remote_blocked(self, monkeypatch):
        monkeypatch.setattr(claude_auth, "_CLAUDE_BROWSER_AUTH", True)
        monkeypatch.setattr(claude_auth, "_CLAUDE_BROWSER_AUTH_ALLOW_REMOTE", False)
        request = SimpleNamespace(client=SimpleNamespace(host="192.168.0.3"))
        with pytest.raises(Exception) as exc_info:
            claude_auth.ensure_browser_auth_request_allowed(request)
        assert exc_info.value.status_code == 403
        assert "local-only" in exc_info.value.detail


class TestClaudeCookieExtraction:
    def test_env_cookie_takes_priority(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_COOKIE", "sessionKey=abc; lastActiveOrg=xyz")
        cookie_header, diagnostics = claude_auth.extract_cookie_header_from_browser()
        assert cookie_header == "sessionKey=abc; lastActiveOrg=xyz"
        assert diagnostics == []

    @pytest.mark.parametrize(
        "domain",
        ["claude.ai", ".claude.ai", "api.anthropic.com", ".api.anthropic.com", "sub.claude.ai"],
    )
    def test_matching_domains(self, domain):
        assert claude_auth._is_claude_domain(domain) is True

    @pytest.mark.parametrize("domain", ["example.com", "anthropic.com", "", ".google.com"])
    def test_non_matching_domains(self, domain):
        assert claude_auth._is_claude_domain(domain) is False

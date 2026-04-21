from __future__ import annotations

from types import SimpleNamespace

from api.chat import _get_profile_from_request


class _AuthStub:
    def is_browser_token(self, token):
        return token == "gemini-browser"

    def parse_browser_token(self, token):
        return True, "token-profile"

    def ensure_browser_auth_request_allowed(self, request):
        return None


def test_provider_specific_profile_header_wins():
    request = SimpleNamespace(headers={"gemini-profile": "header-profile"}, client=None)
    profile = _get_profile_from_request(request, "gemini", _AuthStub(), "gemini-browser")
    assert profile == "header-profile"

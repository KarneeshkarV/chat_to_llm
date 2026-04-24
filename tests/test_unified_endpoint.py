from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import providers
from providers import (
    ProviderEntry,
    _REGISTRY,
    _matches_chatgpt,
    _matches_claude,
    _matches_gemini,
    _matches_grok,
    register_provider,
    resolve_provider_for_model,
)


# ---------------------------------------------------------------------------
# A. Predicate correctness
# ---------------------------------------------------------------------------


class TestMatchesChatGPT:
    @pytest.mark.parametrize(
        "model",
        [
            "gpt-4o",
            "gpt-4",
            "gpt-3.5",
            "o1-mini",
            "o1-preview",
            "o3",
            "o3-mini",
            "o4-mini",
            "text-davinci-002-render-sha",
            "chatgpt-latest",
            "auto",
        ],
    )
    def test_canonical_names_match(self, model):
        assert _matches_chatgpt(model) is True

    def test_case_insensitive(self):
        assert _matches_chatgpt("GPT-4O") is True
        assert _matches_chatgpt("ChatGPT-Latest") is True
        assert _matches_chatgpt("AUTO") is True

    @pytest.mark.parametrize("model", ["", None])
    def test_empty_inputs(self, model):
        assert _matches_chatgpt(model) is False

    @pytest.mark.parametrize(
        "model",
        ["claude-sonnet", "claude-3-5-sonnet", "gemini-pro", "grok-3"],
    )
    def test_other_providers_dont_match(self, model):
        assert _matches_chatgpt(model) is False


class TestMatchesClaude:
    @pytest.mark.parametrize(
        "model",
        ["claude-sonnet-4-0", "claude-3-5-sonnet", "Claude-Opus"],
    )
    def test_canonical_names_match(self, model):
        assert _matches_claude(model) is True

    @pytest.mark.parametrize("model", ["", None])
    def test_empty_inputs(self, model):
        assert _matches_claude(model) is False

    @pytest.mark.parametrize(
        "model",
        ["gpt-4o", "gemini-pro", "grok-3", "auto", "o1-mini"],
    )
    def test_other_providers_dont_match(self, model):
        assert _matches_claude(model) is False


class TestMatchesGemini:
    @pytest.mark.parametrize(
        "model",
        ["gemini-3-flash", "gemini-pro", "Gemini-2-Pro"],
    )
    def test_canonical_names_match(self, model):
        assert _matches_gemini(model) is True

    @pytest.mark.parametrize("model", ["", None])
    def test_empty_inputs(self, model):
        assert _matches_gemini(model) is False

    @pytest.mark.parametrize(
        "model",
        ["gpt-4o", "claude-3", "grok-3", "auto"],
    )
    def test_other_providers_dont_match(self, model):
        assert _matches_gemini(model) is False


class TestMatchesGrok:
    @pytest.mark.parametrize(
        "model",
        ["grok-3", "grok-4", "grok-4.2", "grok-beta", "Grok-Beta"],
    )
    def test_canonical_names_match(self, model):
        assert _matches_grok(model) is True

    @pytest.mark.parametrize("model", ["", None])
    def test_empty_inputs(self, model):
        assert _matches_grok(model) is False

    @pytest.mark.parametrize(
        "model",
        ["gpt-4o", "claude-3", "gemini-pro", "auto"],
    )
    def test_other_providers_dont_match(self, model):
        assert _matches_grok(model) is False


# ---------------------------------------------------------------------------
# B. resolve_provider_for_model dispatch
# ---------------------------------------------------------------------------


class TestResolveProvider:
    def test_resolves_chatgpt(self):
        entry = resolve_provider_for_model("gpt-4o")
        assert entry is not None
        assert entry.name == "chatgpt"

    def test_resolves_claude(self):
        entry = resolve_provider_for_model("claude-sonnet-4-0")
        assert entry is not None
        assert entry.name == "claude"

    def test_resolves_gemini(self):
        entry = resolve_provider_for_model("gemini-pro")
        assert entry is not None
        assert entry.name == "gemini"

    def test_resolves_grok(self):
        entry = resolve_provider_for_model("grok-3")
        assert entry is not None
        assert entry.name == "grok"

    @pytest.mark.parametrize("model", ["unknown-xyz", "", None])
    def test_unknown_model_returns_none(self, model):
        assert resolve_provider_for_model(model) is None

    def test_registration_order_respected_catch_all(self):
        # Install a catch-all provider AT THE END of the registry and verify
        # gpt-4o still resolves to chatgpt (earlier entry wins).
        existing_entry = _REGISTRY["chatgpt"]
        catch_all = ProviderEntry(
            auth=existing_entry.auth,
            service=existing_entry.service,
            formatter=existing_entry.formatter,
            name="catchall",
            matches=lambda m: True,
        )
        try:
            register_provider("catchall", catch_all)
            entry = resolve_provider_for_model("gpt-4o")
            assert entry is not None
            assert entry.name == "chatgpt"
            # Only truly unknown names fall through to catch-all.
            fallthrough = resolve_provider_for_model("totally-unknown-name")
            assert fallthrough is not None
            assert fallthrough.name == "catchall"
        finally:
            _REGISTRY.pop("catchall", None)

    def test_insertion_order(self):
        # Registry should iterate in chatgpt -> claude -> gemini -> grok order.
        names = list(_REGISTRY.keys())
        assert names[:4] == ["chatgpt", "claude", "gemini", "grok"]


# ---------------------------------------------------------------------------
# C. Unified route integration
# ---------------------------------------------------------------------------


class _FakeService:
    def __init__(self, *args, **kwargs) -> None:
        self.closed = False

    async def close_client(self) -> None:
        self.closed = True


@pytest.fixture
def client_and_captures(monkeypatch):
    """Return (TestClient, captures_list) with _process_chat stubbed.

    captures_list collects the provider_name passed to _process_chat so
    tests can assert dispatch correctness.
    """
    # Import inside fixture so we don't trigger app construction at import time
    # if any future test needs to monkeypatch first.
    from app import app
    import api.chat as api_chat

    captures: list[dict] = []

    async def fake_process_chat(provider_name, request_data, req_token, profile=None):
        captures.append(
            {
                "provider_name": provider_name,
                "request_data": request_data,
                "req_token": req_token,
                "profile": profile,
            }
        )
        stub_response = {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "stub"}}],
        }
        return _FakeService(), stub_response

    monkeypatch.setattr(api_chat, "_process_chat", fake_process_chat)

    # Also neutralize _get_profile_from_request so no real auth work runs.
    def fake_profile(request, provider_name, auth, req_token):
        return None

    monkeypatch.setattr(api_chat, "_get_profile_from_request", fake_profile)

    client = TestClient(app)
    return client, captures


class TestUnifiedRoute:
    def test_dispatches_chatgpt(self, client_and_captures):
        client, captures = client_and_captures
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer fake-token"},
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 200, resp.text
        assert len(captures) == 1
        assert captures[0]["provider_name"] == "chatgpt"
        body = resp.json()
        assert body["choices"][0]["message"]["content"] == "stub"

    def test_dispatches_claude(self, client_and_captures):
        client, captures = client_and_captures
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer fake-token"},
            json={"model": "claude-sonnet-4-0", "messages": []},
        )
        assert resp.status_code == 200
        assert captures[0]["provider_name"] == "claude"

    def test_dispatches_gemini(self, client_and_captures):
        client, captures = client_and_captures
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer fake-token"},
            json={"model": "gemini-pro", "messages": []},
        )
        assert resp.status_code == 200
        assert captures[0]["provider_name"] == "gemini"

    def test_dispatches_grok(self, client_and_captures):
        client, captures = client_and_captures
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer fake-token"},
            json={"model": "grok-4", "messages": []},
        )
        assert resp.status_code == 200
        assert captures[0]["provider_name"] == "grok"

    def test_unknown_model_returns_400_with_provider_list(self, client_and_captures):
        client, captures = client_and_captures
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer fake-token"},
            json={"model": "bogus-42", "messages": []},
        )
        assert resp.status_code == 400
        assert captures == []  # never dispatched
        detail = resp.json().get("detail", "")
        assert "bogus-42" in detail
        # Should mention the known providers
        for name in ("chatgpt", "claude", "gemini", "grok"):
            assert name in detail

    def test_empty_body_returns_400(self, client_and_captures):
        # Empty JSON body: model defaults to "", which is unknown.
        client, captures = client_and_captures
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer fake-token"},
            json={},
        )
        assert resp.status_code == 400
        assert captures == []

    def test_missing_model_field_returns_400(self, client_and_captures):
        client, captures = client_and_captures
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer fake-token"},
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 400
        assert captures == []

    def test_invalid_json_body_returns_400(self, client_and_captures):
        client, captures = client_and_captures
        resp = client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": "Bearer fake-token",
                "Content-Type": "application/json",
            },
            content=b"this is not json {{{",
        )
        assert resp.status_code == 400
        assert captures == []
        detail = resp.json().get("detail")
        # The handler wraps the message as {"error": "Invalid JSON body"}
        if isinstance(detail, dict):
            assert detail.get("error") == "Invalid JSON body"
        else:
            assert "Invalid JSON" in str(detail)

    def test_unauthenticated_request_rejected(self, client_and_captures):
        client, captures = client_and_captures
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": []},
        )
        # FastAPI's HTTPBearer returns 403 by default when the Authorization
        # header is missing. Accept either 401 or 403 to avoid hard-coding.
        assert resp.status_code in (401, 403)
        assert captures == []

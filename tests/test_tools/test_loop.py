from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from providers.tools import loop as loop_mod


class _TurnScript:
    """Returns successive scripted texts on each call."""

    def __init__(self, texts: List[str]) -> None:
        self._texts = list(texts)
        self.calls: List[Dict[str, Any]] = []

    async def __call__(
        self,
        provider_name: str,
        request_data: Dict[str, Any],
        req_token,
        profile,
    ) -> str:
        self.calls.append(
            {
                "provider": provider_name,
                "messages": list(request_data.get("messages", [])),
                "stream": request_data.get("stream"),
                "has_tools": "tools" in request_data,
                "has_enable_tools": "enable_tools" in request_data,
            }
        )
        if not self._texts:
            return ""
        return self._texts.pop(0)


@pytest.fixture
def patch_run_one_turn(monkeypatch):
    def install(script: _TurnScript) -> _TurnScript:
        monkeypatch.setattr(loop_mod, "_run_one_turn", script)
        return script

    return install


@pytest.fixture
def stub_web_search(monkeypatch):
    async def fake_run_tool(name: str, arguments: Dict[str, Any]):
        return {"results": [{"title": "T", "url": "U", "snippet": "S"}]}

    monkeypatch.setattr(loop_mod, "run_tool", fake_run_tool)


class TestRunLoop:
    def test_returns_immediately_when_no_tool_call(
        self, patch_run_one_turn, stub_web_search
    ):
        script = patch_run_one_turn(_TurnScript(["Just a normal answer."]))
        request = {
            "model": "claude-3-5-sonnet-latest",
            "messages": [{"role": "user", "content": "hi"}],
        }
        final, model, _msgs, iters = asyncio.run(
            loop_mod.run_loop("claude", request, "tok", None)
        )
        assert final == "Just a normal answer."
        assert model == "claude-3-5-sonnet-latest"
        assert iters == 1
        assert len(script.calls) == 1

    def test_runs_tool_then_returns_final(
        self, patch_run_one_turn, stub_web_search
    ):
        first = (
            "Let me search.\n"
            "```tool_call\n"
            '{"name": "web_search", "arguments": {"query": "spacex"}}\n'
            "```"
        )
        second = "Final grounded answer."
        script = patch_run_one_turn(_TurnScript([first, second]))
        request = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "what did spacex do?"}],
        }
        final, _model, msgs, iters = asyncio.run(
            loop_mod.run_loop("chatgpt", request, None, None)
        )
        assert final == "Final grounded answer."
        assert iters == 2
        assert len(script.calls) == 2
        assert any(
            m.get("role") == "user" and "tool_result" in (m.get("content") or "")
            for m in msgs
        )

    def test_strips_tools_and_enable_tools_per_turn(
        self, patch_run_one_turn, stub_web_search
    ):
        script = patch_run_one_turn(_TurnScript(["done"]))
        request = {
            "model": "grok-3",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function"}],
            "tool_choice": "auto",
            "enable_tools": True,
            "stream": True,
        }
        asyncio.run(loop_mod.run_loop("grok", request, None, None))
        call = script.calls[0]
        assert call["has_tools"] is False
        assert call["has_enable_tools"] is False
        assert call["stream"] is False

    def test_respects_iteration_cap(
        self, patch_run_one_turn, stub_web_search, monkeypatch
    ):
        monkeypatch.setattr(loop_mod, "_MAX_ITERS", 3)
        always_call_tool = (
            "Looking again.\n"
            "```tool_call\n"
            '{"name": "web_search", "arguments": {"query": "x"}}\n'
            "```"
        )
        script = patch_run_one_turn(
            _TurnScript([always_call_tool, always_call_tool, always_call_tool])
        )
        request = {
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": "hi"}],
        }
        final, _model, _msgs, iters = asyncio.run(
            loop_mod.run_loop("gemini", request, None, None)
        )
        assert iters == 3
        assert len(script.calls) == 3
        assert final == always_call_tool

    def test_invalid_tool_call_feeds_error_back(
        self, patch_run_one_turn, stub_web_search
    ):
        bad_call = "```tool_call\n{not valid json}\n```"
        recovery = "Sorry — recovered."
        patch_run_one_turn(_TurnScript([bad_call, recovery]))
        request = {
            "model": "claude-3-5-sonnet-latest",
            "messages": [{"role": "user", "content": "hi"}],
        }
        final, _model, msgs, iters = asyncio.run(
            loop_mod.run_loop("claude", request, None, None)
        )
        assert final == "Sorry — recovered."
        assert iters == 2
        assert any(
            m.get("role") == "user"
            and "tool_result" in (m.get("content") or "")
            and "error" in (m.get("content") or "")
            for m in msgs
        )

    def test_injects_tool_system_prompt(
        self, patch_run_one_turn, stub_web_search
    ):
        script = patch_run_one_turn(_TurnScript(["ok"]))
        request = {
            "model": "claude-3-5-sonnet-latest",
            "messages": [{"role": "user", "content": "hi"}],
        }
        asyncio.run(loop_mod.run_loop("claude", request, None, None))
        sent = script.calls[0]["messages"]
        assert sent[0]["role"] == "system"
        assert "tool_call" in sent[0]["content"]
        assert "web_search" in sent[0]["content"]

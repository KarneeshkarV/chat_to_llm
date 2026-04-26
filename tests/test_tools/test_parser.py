from __future__ import annotations

from providers.tools.parser import extract_tool_call, strip_tool_call


class TestExtractToolCall:
    def test_extracts_well_formed_call(self):
        text = (
            "Sure, let me look that up.\n"
            "```tool_call\n"
            '{"name": "web_search", "arguments": {"query": "spacex launch"}}\n'
            "```\n"
        )
        call = extract_tool_call(text)
        assert call is not None
        assert call.name == "web_search"
        assert call.arguments == {"query": "spacex launch"}

    def test_returns_none_for_plain_code_block(self):
        text = (
            "Here's some code:\n"
            "```python\n"
            "print('hi')\n"
            "```\n"
        )
        assert extract_tool_call(text) is None

    def test_returns_none_for_empty_input(self):
        assert extract_tool_call("") is None
        assert extract_tool_call(None) is None  # type: ignore[arg-type]

    def test_invalid_json_returns_invalid_marker(self):
        text = "```tool_call\n{not json}\n```"
        call = extract_tool_call(text)
        assert call is not None
        assert call.name == "__invalid__"
        assert "JSON decode failed" in call.arguments["_error"]

    def test_missing_name_returns_invalid_marker(self):
        text = '```tool_call\n{"arguments": {}}\n```'
        call = extract_tool_call(text)
        assert call is not None
        assert call.name == "__invalid__"

    def test_arguments_must_be_object(self):
        text = '```tool_call\n{"name": "x", "arguments": "oops"}\n```'
        call = extract_tool_call(text)
        assert call is not None
        assert call.name == "__invalid__"

    def test_extracts_first_call_when_multiple(self):
        text = (
            "```tool_call\n"
            '{"name": "first", "arguments": {}}\n'
            "```\n"
            "```tool_call\n"
            '{"name": "second", "arguments": {}}\n'
            "```\n"
        )
        call = extract_tool_call(text)
        assert call is not None
        assert call.name == "first"


class TestStripToolCall:
    def test_strip_removes_block(self):
        text = (
            "Looking it up.\n"
            "```tool_call\n"
            '{"name": "web_search", "arguments": {"query": "x"}}\n'
            "```"
        )
        call = extract_tool_call(text)
        assert call is not None
        stripped = strip_tool_call(text, call)
        assert "tool_call" not in stripped
        assert "Looking it up." in stripped

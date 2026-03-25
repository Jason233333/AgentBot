"""Tests for nanobot.providers.xml_tool_parser."""

import json

import pytest

from nanobot.providers.xml_tool_parser import (
    format_tool_definitions,
    format_tool_result,
    parse_tool_calls,
)


# =========================================================================
# format_tool_definitions
# =========================================================================


class TestFormatToolDefinitions:
    def test_empty_tools(self):
        assert format_tool_definitions([]) == ""

    def test_single_tool(self):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file from disk",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                },
            }
        ]
        result = format_tool_definitions(tools)
        assert "read_file" in result
        assert "Read a file from disk" in result
        assert "<tool_call" in result
        assert "Tool Use Instructions" in result

    def test_multiple_tools(self):
        tools = [
            {"type": "function", "function": {"name": "tool_a", "description": "A"}},
            {"type": "function", "function": {"name": "tool_b", "description": "B"}},
        ]
        result = format_tool_definitions(tools)
        assert "tool_a" in result
        assert "tool_b" in result

    def test_tool_without_description(self):
        tools = [{"type": "function", "function": {"name": "ping", "parameters": {}}}]
        result = format_tool_definitions(tools)
        assert "ping" in result


# =========================================================================
# parse_tool_calls
# =========================================================================


class TestParseToolCalls:
    def test_no_tool_calls(self):
        text = "Hello, I can help you with that."
        clean, calls = parse_tool_calls(text)
        assert clean == text
        assert calls == []

    def test_empty_input(self):
        clean, calls = parse_tool_calls("")
        assert clean == ""
        assert calls == []

    def test_single_tool_call(self):
        text = (
            'I will read the file.\n'
            '<tool_call id="abc123" name="read_file">'
            '{"path": "/tmp/test.txt"}'
            '</tool_call>'
        )
        clean, calls = parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0].name == "read_file"
        assert calls[0].id == "abc123"
        assert calls[0].arguments == {"path": "/tmp/test.txt"}
        assert "tool_call" not in clean
        assert "I will read the file." in clean

    def test_multiple_tool_calls(self):
        text = (
            '<tool_call id="a1" name="read_file">{"path": "a.txt"}</tool_call>\n'
            '<tool_call id="b2" name="write_file">{"path": "b.txt", "content": "hi"}</tool_call>'
        )
        clean, calls = parse_tool_calls(text)
        assert len(calls) == 2
        assert calls[0].name == "read_file"
        assert calls[1].name == "write_file"
        assert calls[1].arguments["content"] == "hi"

    def test_markdown_wrapped_json(self):
        text = (
            '<tool_call id="m1" name="exec">\n'
            '```json\n'
            '{"command": "ls -la"}\n'
            '```\n'
            '</tool_call>'
        )
        clean, calls = parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0].arguments == {"command": "ls -la"}

    def test_single_quoted_attributes(self):
        text = "<tool_call id='x1' name='ping'>{}</tool_call>"
        clean, calls = parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0].id == "x1"
        assert calls[0].name == "ping"

    def test_name_before_id(self):
        """Test alternative attribute order: name before id."""
        text = '<tool_call name="search" id="s1">{"query": "test"}</tool_call>'
        clean, calls = parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0].name == "search"
        assert calls[0].id == "s1"

    def test_malformed_json_fallback(self):
        text = '<tool_call id="bad" name="tool">{not valid json}</tool_call>'
        clean, calls = parse_tool_calls(text)
        assert len(calls) == 1
        # Should still parse, either via json_repair or _raw fallback
        assert calls[0].name == "tool"
        assert isinstance(calls[0].arguments, dict)

    def test_empty_body(self):
        text = '<tool_call id="e1" name="noop"></tool_call>'
        clean, calls = parse_tool_calls(text)
        assert len(calls) == 1
        assert calls[0].arguments == {}

    def test_mixed_text_and_tool_calls(self):
        text = (
            "Let me think about this.\n\n"
            "I'll search for information.\n"
            '<tool_call id="t1" name="web_search">{"query": "python asyncio"}</tool_call>\n\n'
            "And also read a file.\n"
            '<tool_call id="t2" name="read_file">{"path": "main.py"}</tool_call>'
        )
        clean, calls = parse_tool_calls(text)
        assert len(calls) == 2
        assert "Let me think" in clean
        assert "And also read" in clean
        assert "<tool_call" not in clean


# =========================================================================
# format_tool_result
# =========================================================================


class TestFormatToolResult:
    def test_basic_result(self):
        result = format_tool_result("abc", "read_file", "file content here")
        assert '<tool_response id="abc" name="read_file">' in result
        assert "file content here" in result
        assert "</tool_response>" in result

    def test_multiline_result(self):
        content = "line 1\nline 2\nline 3"
        result = format_tool_result("id1", "exec", content)
        assert "line 1\nline 2\nline 3" in result

    def test_json_result(self):
        data = json.dumps({"status": "ok", "count": 42})
        result = format_tool_result("j1", "api_call", data)
        assert '"status": "ok"' in result

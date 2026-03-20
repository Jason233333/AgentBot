"""Integration tests for the zero-token (Claude Web) core flow.

Tests the full provider pipeline: message conversion → (mocked) web response → parse back.
The browser layer is mocked so tests run without a real Chrome instance.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot.providers.claude_web_provider import ClaudeWebProvider
from nanobot.providers.fallback_provider import FallbackProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider():
    """Create a ClaudeWebProvider with mocked browser client."""
    p = ClaudeWebProvider(
        session_key="test-session",
        organization_id="org-123",
        default_model="claude-sonnet-4-6",
    )
    return p


def _mock_client(provider: ClaudeWebProvider, response_text: str) -> None:
    """Patch the internal client to return a canned response."""
    provider._client.create_conversation = AsyncMock(return_value="conv-001")
    provider._client.send_message = AsyncMock(return_value=response_text)
    provider._client.ensure_browser = AsyncMock()


# ---------------------------------------------------------------------------
# 1. Simple conversation (no tool calls)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_chat(provider):
    """Web returns plain text → LLMResponse with content, no tool_calls."""
    _mock_client(provider, "Hello! How can I help you today?")

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hi there"},
    ]
    resp = await provider.chat(messages)

    assert resp.content == "Hello! How can I help you today?"
    assert resp.finish_reason == "stop"
    assert resp.tool_calls == []
    provider._client.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# 2. Tool call response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_response(provider):
    """Web returns text with <tool_call> XML → parsed ToolCallRequests."""
    web_response = (
        "Let me read that file for you.\n"
        '<tool_call id="tc1" name="read_file">'
        '{"path": "/tmp/test.txt"}'
        "</tool_call>"
    )
    _mock_client(provider, web_response)

    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        },
    ]
    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Read /tmp/test.txt"},
    ]
    resp = await provider.chat(messages, tools=tools)

    assert resp.finish_reason == "tool_calls"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "read_file"
    assert resp.tool_calls[0].arguments == {"path": "/tmp/test.txt"}
    assert "Let me read" in (resp.content or "")

    # Verify tool definitions were injected in the prompt
    sent_prompt = provider._client.send_message.call_args.kwargs.get(
        "prompt", provider._client.send_message.call_args[1].get("prompt", "")
    )
    assert "read_file" in sent_prompt
    assert "Tool Use Instructions" in sent_prompt


# ---------------------------------------------------------------------------
# 3. Tool result continuation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_result_continuation(provider):
    """After tool execution, tool results are sent as XML and response is parsed."""
    _mock_client(provider, "The file contains: hello world")

    # Pre-populate conversation mapping so it reuses the conversation
    provider._conversations[provider._get_session_key([
        {"role": "system", "content": "You are an assistant."}
    ])] = "conv-001"

    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Read the file"},
        {
            "role": "assistant",
            "content": "Let me read that.",
            "tool_calls": [
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "/tmp/test.txt"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc1",
            "name": "read_file",
            "content": "hello world",
        },
    ]
    resp = await provider.chat(messages)

    assert resp.content == "The file contains: hello world"
    assert resp.finish_reason == "stop"

    # Verify the prompt sent contains tool_response XML
    sent_prompt = provider._client.send_message.call_args.kwargs.get(
        "prompt", provider._client.send_message.call_args[1].get("prompt", "")
    )
    assert "<tool_response" in sent_prompt
    assert "hello world" in sent_prompt
    assert "read_file" in sent_prompt


# ---------------------------------------------------------------------------
# 4. Multiple tool calls in one response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_tool_calls(provider):
    """Web returns multiple <tool_call> tags → multiple ToolCallRequests."""
    web_response = (
        "I'll search and read.\n"
        '<tool_call id="t1" name="web_search">{"query": "python"}</tool_call>\n'
        '<tool_call id="t2" name="read_file">{"path": "main.py"}</tool_call>'
    )
    _mock_client(provider, web_response)

    messages = [
        {"role": "system", "content": "assistant"},
        {"role": "user", "content": "Search and read"},
    ]
    resp = await provider.chat(messages)

    assert resp.finish_reason == "tool_calls"
    assert len(resp.tool_calls) == 2
    assert resp.tool_calls[0].name == "web_search"
    assert resp.tool_calls[1].name == "read_file"


# ---------------------------------------------------------------------------
# 5. Image attachment conversion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_attachment(provider):
    """Base64 image_url in user message → claude.ai attachment format."""
    import base64

    # Minimal PNG (1x1 pixel)
    fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()
    data_url = f"data:image/png;base64,{fake_png}"

    _mock_client(provider, "I can see the image.")

    messages = [
        {"role": "system", "content": "assistant"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is this?"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]
    resp = await provider.chat(messages)

    assert resp.content == "I can see the image."
    # Verify attachment was passed to send_message
    call_kwargs = provider._client.send_message.call_args
    attachments = call_kwargs.kwargs.get("attachments", call_kwargs[1].get("attachments", []))
    assert len(attachments) == 1
    assert attachments[0]["file_type"] == "image/png"
    assert attachments[0]["file_name"] == "image.png"


# ---------------------------------------------------------------------------
# 6. Conversation reuse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_reuse(provider):
    """Second call with same session reuses existing conversation_id."""
    _mock_client(provider, "Response 1")

    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Hello"},
    ]

    # First call — creates conversation
    await provider.chat(messages)
    provider._client.create_conversation.assert_awaited_once()

    # Second call — reuses conversation
    _mock_client(provider, "Response 2")
    # Keep existing conversation mapping
    provider._client.create_conversation = AsyncMock(return_value="conv-002")
    await provider.chat(messages)
    # create_conversation should NOT be called again
    provider._client.create_conversation.assert_not_awaited()


# ---------------------------------------------------------------------------
# 7. Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_error_returns_error_response(provider):
    """Browser failure → LLMResponse with finish_reason='error'."""
    provider._client.create_conversation = AsyncMock(
        side_effect=ConnectionError("Chrome not running")
    )
    provider._client.ensure_browser = AsyncMock()

    messages = [
        {"role": "system", "content": "assistant"},
        {"role": "user", "content": "hello"},
    ]
    resp = await provider.chat(messages)

    assert resp.finish_reason == "error"
    assert "Chrome not running" in resp.content


# ---------------------------------------------------------------------------
# 8. Fallback provider flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_provider_uses_secondary_on_error():
    """FallbackProvider: primary fails → secondary succeeds."""
    primary = AsyncMock(spec=["chat", "get_default_model", "generation"])
    primary.chat = AsyncMock(return_value=LLMResponse(
        content="API error: 429 rate limited",
        finish_reason="error",
    ))
    primary.get_default_model = MagicMock(return_value="claude-sonnet-4-6")
    primary.generation = MagicMock()

    secondary = AsyncMock(spec=["chat", "get_default_model", "generation"])
    secondary.chat = AsyncMock(return_value=LLMResponse(
        content="Hello from Claude Web!",
        finish_reason="stop",
    ))

    fb = FallbackProvider(primary=primary, secondary=secondary)

    resp = await fb.chat(
        messages=[{"role": "user", "content": "hi"}],
    )

    assert resp.content == "Hello from Claude Web!"
    assert resp.finish_reason == "stop"
    assert resp.recovered_from_error is True
    primary.chat.assert_awaited_once()
    secondary.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_provider_uses_primary_when_ok():
    """FallbackProvider: primary succeeds → secondary not called."""
    primary = AsyncMock(spec=["chat", "get_default_model", "generation"])
    primary.chat = AsyncMock(return_value=LLMResponse(
        content="Hello from API!",
        finish_reason="stop",
    ))
    primary.get_default_model = MagicMock(return_value="claude-sonnet-4-6")
    primary.generation = MagicMock()

    secondary = AsyncMock(spec=["chat", "get_default_model", "generation"])
    secondary.chat = AsyncMock()

    fb = FallbackProvider(primary=primary, secondary=secondary)

    resp = await fb.chat(
        messages=[{"role": "user", "content": "hi"}],
    )

    assert resp.content == "Hello from API!"
    primary.chat.assert_awaited_once()
    secondary.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_provider_primary_exception():
    """FallbackProvider: primary raises exception → secondary is tried."""
    primary = AsyncMock(spec=["chat", "get_default_model", "generation"])
    primary.chat = AsyncMock(side_effect=ConnectionError("network down"))
    primary.get_default_model = MagicMock(return_value="claude-sonnet-4-6")
    primary.generation = MagicMock()

    secondary = AsyncMock(spec=["chat", "get_default_model", "generation"])
    secondary.chat = AsyncMock(return_value=LLMResponse(
        content="Fallback worked",
        finish_reason="stop",
    ))

    fb = FallbackProvider(primary=primary, secondary=secondary)

    resp = await fb.chat(
        messages=[{"role": "user", "content": "hi"}],
    )

    assert resp.content == "Fallback worked"
    assert resp.recovered_from_error is True


# ---------------------------------------------------------------------------
# 9. Message conversion edge cases
# ---------------------------------------------------------------------------


class TestMessageConversion:
    """Test _convert_messages directly for edge cases."""

    def setup_method(self):
        self.provider = ClaudeWebProvider(default_model="claude-sonnet-4-6")

    def test_system_and_user_only(self):
        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hello"},
        ]
        prompt, attachments = self.provider._convert_messages(messages)
        assert "Be helpful." in prompt
        assert "[User]: Hello" in prompt
        assert attachments == []

    def test_tool_defs_injected(self):
        messages = [
            {"role": "system", "content": "System."},
            {"role": "user", "content": "Do something."},
        ]
        tools = [
            {"type": "function", "function": {"name": "ping", "description": "Ping"}},
        ]
        prompt, _ = self.provider._convert_messages(messages, tools)
        assert "ping" in prompt
        assert "Tool Use Instructions" in prompt

    def test_history_with_tool_calls(self):
        """History containing assistant tool_calls + tool results → XML in prompt."""
        messages = [
            {"role": "system", "content": "Sys."},
            {"role": "user", "content": "Read file"},
            {
                "role": "assistant",
                "content": "Reading...",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "tc1", "name": "read_file", "content": "file data"},
            {"role": "user", "content": "Thanks, now summarize"},
        ]
        # This has tool results so it goes through _convert_continuation
        prompt, _ = self.provider._convert_messages(messages)
        # Should contain the tool result as XML
        assert "<tool_response" in prompt
        assert "file data" in prompt

    def test_continuation_sends_only_new_tool_results(self):
        messages = [
            {"role": "system", "content": "Sys."},
            {"role": "user", "content": "Do stuff"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "a", "type": "function", "function": {"name": "t1", "arguments": "{}"}},
                    {"id": "b", "type": "function", "function": {"name": "t2", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "a", "name": "t1", "content": "result_a"},
            {"role": "tool", "tool_call_id": "b", "name": "t2", "content": "result_b"},
        ]
        prompt, _ = self.provider._convert_continuation(messages)
        assert "result_a" in prompt
        assert "result_b" in prompt
        assert "Please proceed" in prompt
        # Should NOT contain system prompt or user message
        assert "Sys." not in prompt

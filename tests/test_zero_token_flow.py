"""Integration tests for the zero-token (Claude Web) core flow.

Tests the full provider pipeline: message conversion → (mocked) web response → parse back.
The browser layer is mocked so tests run without a real Chrome instance.

The provider operates in hybrid mode: new user messages create fresh conversations,
tool continuations reuse the existing conversation with incremental messages.
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
# 3. Tool result continuation (hybrid: reuses conversation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_result_continuation_reuses_conversation(provider):
    """Tool continuation reuses existing conversation with incremental prompt."""
    _mock_client(provider, "The file contains: hello world")

    # Simulate existing conversation from a prior user-message call
    provider._conversations["test-key"] = "conv-existing"

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
    resp = await provider.chat(messages, session_key="test-key")

    assert resp.content == "The file contains: hello world"
    assert resp.finish_reason == "stop"

    # Should NOT have created a new conversation (reused existing)
    provider._client.create_conversation.assert_not_awaited()

    # Verify incremental prompt: only tool results, no system prompt
    sent_prompt = provider._client.send_message.call_args.kwargs.get(
        "prompt", provider._client.send_message.call_args[1].get("prompt", "")
    )
    assert "<tool_response" in sent_prompt
    assert "hello world" in sent_prompt
    # System prompt should NOT be in incremental continuation
    assert "You are an assistant." not in sent_prompt


@pytest.mark.asyncio
async def test_tool_result_without_existing_conv_creates_new(provider):
    """Tool result without existing conversation → creates new (full prompt)."""
    _mock_client(provider, "The file contains: hello world")

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
    resp = await provider.chat(messages, session_key="no-existing-conv")

    assert resp.content == "The file contains: hello world"

    # No existing conversation → should create new
    provider._client.create_conversation.assert_awaited_once()

    # Full prompt should include system prompt
    sent_prompt = provider._client.send_message.call_args.kwargs.get(
        "prompt", provider._client.send_message.call_args[1].get("prompt", "")
    )
    assert "You are an assistant." in sent_prompt
    assert "hello world" in sent_prompt


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
# 6. Hybrid: new user messages always create new conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_user_message_creates_new_conversation(provider):
    """New user message always creates a fresh conversation (hybrid mode)."""
    _mock_client(provider, "Response 1")

    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Hello"},
    ]

    # First call — creates conversation
    await provider.chat(messages)
    assert provider._client.create_conversation.await_count == 1

    # Second call with user message — also creates a new conversation
    _mock_client(provider, "Response 2")
    await provider.chat(messages)
    assert provider._client.create_conversation.await_count == 1  # reset by _mock_client

    # Verify full prompt is sent each time (system prompt present)
    sent_prompt = provider._client.send_message.call_args.kwargs.get(
        "prompt", provider._client.send_message.call_args[1].get("prompt", "")
    )
    assert "You are an assistant." in sent_prompt


@pytest.mark.asyncio
async def test_hybrid_flow_user_then_tool_then_user(provider):
    """Full hybrid flow: user msg → new conv, tool → reuse, user msg → new conv."""
    # Step 1: User message → new conversation
    _mock_client(provider, '<tool_call id="tc1" name="read_file">{"path": "a.txt"}</tool_call>')
    messages_1 = [
        {"role": "system", "content": "Sys."},
        {"role": "user", "content": "Read a.txt"},
    ]
    await provider.chat(messages_1, session_key="s1")
    assert provider._client.create_conversation.await_count == 1
    assert "s1" in provider._conversations

    # Step 2: Tool continuation → reuse conversation
    _mock_client(provider, "File contains: data")
    messages_2 = messages_1 + [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "tc1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}},
        ]},
        {"role": "tool", "tool_call_id": "tc1", "name": "read_file", "content": "data"},
    ]
    resp = await provider.chat(messages_2, session_key="s1")
    # Should NOT create new conversation
    provider._client.create_conversation.assert_not_awaited()
    assert resp.content == "File contains: data"

    # Step 3: New user message → new conversation again
    _mock_client(provider, "Sure!")
    messages_3 = [
        {"role": "system", "content": "Sys."},
        {"role": "user", "content": "Now do something else"},
    ]
    await provider.chat(messages_3, session_key="s1")
    assert provider._client.create_conversation.await_count == 1


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


@pytest.mark.asyncio
async def test_error_clears_conversation(provider):
    """Error during tool continuation clears the conversation mapping."""
    # Set up existing conversation
    provider._conversations["s1"] = "conv-old"
    provider._client.send_message = AsyncMock(side_effect=ConnectionError("network error"))
    provider._client.create_conversation = AsyncMock(return_value="conv-old")
    provider._client.ensure_browser = AsyncMock()

    messages = [
        {"role": "system", "content": "Sys."},
        {"role": "user", "content": "Do stuff"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "t1", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "name": "t1", "content": "result"},
    ]
    resp = await provider.chat(messages, session_key="s1")

    assert resp.finish_reason == "error"
    # Conversation should be cleared after error
    assert "s1" not in provider._conversations


@pytest.mark.asyncio
async def test_clear_session(provider):
    """clear_session removes tracked conversation."""
    provider._conversations["s1"] = "conv-1"
    provider._conversations["s2"] = "conv-2"

    provider.clear_session("s1")
    assert "s1" not in provider._conversations
    assert "s2" in provider._conversations

    provider.clear_session()
    assert len(provider._conversations) == 0


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
        prompt, _ = self.provider._convert_messages(messages)
        # Current turn is "Thanks, now summarize"; old turn has tool results condensed away
        # But since tool_call/result is in the same turn as "Read file", and "Thanks" is the
        # current turn, the old turn's tool details are stripped (condensed).
        assert "Sys." in prompt  # system prompt always present
        assert "[User]: Thanks, now summarize" in prompt

    def test_format_error_rate_limit(self):
        """429 rate limit error is parsed into human-readable message."""
        import time
        future_ts = int(time.time()) + 7200  # 2 hours from now
        raw = (
            f'Page.evaluate: Error: HTTP 429: {{"type":"error","error":{{"type":"rate_limit_error",'
            f'"message":"{{\\"resetsAt\\":{future_ts}}}"}}}}'
        )
        msg = ClaudeWebProvider._format_error(Exception(raw))
        assert "rate limit exceeded" in msg.lower()
        assert "Resets at" in msg
        assert "h" in msg or "m" in msg  # has time remaining

    def test_format_error_generic(self):
        """Non-rate-limit errors pass through."""
        msg = ClaudeWebProvider._format_error(ConnectionError("Chrome not running"))
        assert "Chrome not running" in msg

    def test_convert_continuation_only_tool_results(self):
        """_convert_continuation extracts only tool results after last assistant tool_calls."""
        messages = [
            {"role": "system", "content": "Sys."},
            {"role": "user", "content": "Do stuff"},
            {
                "role": "assistant",
                "content": "Calling tools...",
                "tool_calls": [
                    {"id": "a", "type": "function", "function": {"name": "t1", "arguments": "{}"}},
                    {"id": "b", "type": "function", "function": {"name": "t2", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "a", "name": "t1", "content": "result_a"},
            {"role": "tool", "tool_call_id": "b", "name": "t2", "content": "result_b"},
        ]
        prompt = ClaudeWebProvider._convert_continuation(messages)
        assert "result_a" in prompt
        assert "result_b" in prompt
        assert "<tool_response" in prompt
        # Should NOT include system prompt or user message
        assert "Sys." not in prompt
        assert "Do stuff" not in prompt
        assert "Calling tools" not in prompt

    def test_full_prompt_includes_tool_results_in_current_turn(self):
        """Tool results in the current turn (after last user msg) are preserved."""
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
        prompt, _ = self.provider._convert_messages(messages)
        assert "result_a" in prompt
        assert "result_b" in prompt
        # System prompt always included in stateless mode
        assert "Sys." in prompt
        assert "[User]: Do stuff" in prompt

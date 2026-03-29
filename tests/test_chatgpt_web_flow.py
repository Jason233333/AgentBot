"""Integration tests for the ChatGPT Web zero-token provider.

Browser layer is mocked so tests run without a real Chrome instance.
Tests cover: simple chat, tool calls, hybrid conversation mode, error handling.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from nanobot.providers.base import LLMResponse
from nanobot.providers.chatgpt_web_provider import ChatGPTWebProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider():
    """ChatGPTWebProvider with mocked browser client."""
    p = ChatGPTWebProvider(default_model="gpt-4o")
    return p


def _mock_client(provider: ChatGPTWebProvider, response_text: str,
                 conv_id: str = "gpt-conv-001", parent_id: str = "msg-001") -> None:
    """Patch the internal client to return a canned response."""
    provider._client.send_message = AsyncMock(
        return_value=(response_text, conv_id, parent_id)
    )
    provider._client.ensure_browser = AsyncMock()


# ---------------------------------------------------------------------------
# 1. Simple conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_chat(provider):
    """Web returns plain text → LLMResponse with content, no tool_calls."""
    _mock_client(provider, "Hello from GPT!")

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hi there"},
    ]
    resp = await provider.chat(messages)

    assert resp.content == "Hello from GPT!"
    assert resp.finish_reason == "stop"
    assert resp.tool_calls == []
    provider._client.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# 2. Tool call response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_response(provider):
    """Web returns XML tool_call → parsed ToolCallRequests."""
    web_response = (
        "Let me read that.\n"
        '<tool_call id="tc1" name="read_file">{"path": "/tmp/test.txt"}</tool_call>'
    )
    _mock_client(provider, web_response)

    tools = [{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }]
    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Read /tmp/test.txt"},
    ]
    resp = await provider.chat(messages, tools=tools)

    assert resp.finish_reason == "tool_calls"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "read_file"
    assert resp.tool_calls[0].arguments == {"path": "/tmp/test.txt"}

    # Tool definitions injected in prompt
    call_args = provider._client.send_message.call_args
    prompt = call_args[0][0] if call_args[0] else call_args.kwargs.get("prompt", "")
    assert "read_file" in prompt
    assert "Tool Use Instructions" in prompt


# ---------------------------------------------------------------------------
# 3. Hybrid: tool result continuation reuses conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_result_continuation_reuses_conversation(provider):
    """Tool continuation reuses existing conversation."""
    _mock_client(provider, "File contains: hello world")

    provider._conversations["test-key"] = {
        "conv_id": "gpt-conv-existing",
        "parent_msg_id": "msg-existing",
    }

    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Read the file"},
        {
            "role": "assistant",
            "content": "Reading...",
            "tool_calls": [{
                "id": "tc1", "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "/tmp/test.txt"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "tc1", "name": "read_file", "content": "hello world"},
    ]
    resp = await provider.chat(messages, session_key="test-key")

    assert resp.content == "File contains: hello world"

    # Should pass existing conv_id to client
    call_kwargs = provider._client.send_message.call_args.kwargs
    assert call_kwargs.get("conversation_id") == "gpt-conv-existing"

    # Incremental prompt: only tool result, no system prompt
    prompt = call_kwargs.get("prompt", "")
    assert "<tool_response" in prompt
    assert "hello world" in prompt
    assert "You are an assistant." not in prompt


@pytest.mark.asyncio
async def test_new_user_message_creates_new_conversation(provider):
    """New user message always creates a new conversation (no conv_id)."""
    _mock_client(provider, "Hello!")

    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Hello"},
    ]
    await provider.chat(messages, session_key="s1")

    call_kwargs = provider._client.send_message.call_args.kwargs
    # conversation_id should be None for new conversation
    assert call_kwargs.get("conversation_id") is None

    # Full prompt includes system content
    prompt = call_kwargs.get("prompt", "")
    assert "You are an assistant." in prompt


@pytest.mark.asyncio
async def test_hybrid_flow_stores_conv_id(provider):
    """After new conversation, conv_id is stored for subsequent tool continuation."""
    _mock_client(provider, "ok", conv_id="gpt-new-001", parent_id="msg-new-001")

    messages = [
        {"role": "system", "content": "Sys."},
        {"role": "user", "content": "Do something"},
    ]
    await provider.chat(messages, session_key="s1")

    assert provider._conversations["s1"]["conv_id"] == "gpt-new-001"
    assert provider._conversations["s1"]["parent_msg_id"] == "msg-new-001"


# ---------------------------------------------------------------------------
# 4. Multiple tool calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_tool_calls(provider):
    """Multiple <tool_call> tags → multiple ToolCallRequests."""
    web_response = (
        "Running tools.\n"
        '<tool_call id="t1" name="search">{"query": "python"}</tool_call>\n'
        '<tool_call id="t2" name="read_file">{"path": "main.py"}</tool_call>'
    )
    _mock_client(provider, web_response)

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
    resp = await provider.chat(messages)

    assert len(resp.tool_calls) == 2
    assert resp.tool_calls[0].name == "search"
    assert resp.tool_calls[1].name == "read_file"


# ---------------------------------------------------------------------------
# 5. Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_error_returns_error_response(provider):
    """Browser failure → LLMResponse with finish_reason='error'."""
    provider._client.send_message = AsyncMock(
        side_effect=ConnectionError("Chrome not running")
    )
    provider._client.ensure_browser = AsyncMock()

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    resp = await provider.chat(messages)

    assert resp.finish_reason == "error"
    assert "Chrome not running" in resp.content


@pytest.mark.asyncio
async def test_error_clears_conversation(provider):
    """Error clears conversation mapping for the session."""
    provider._conversations["s1"] = {"conv_id": "old", "parent_msg_id": "p-old"}
    provider._client.send_message = AsyncMock(side_effect=ConnectionError("net error"))
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
    assert "s1" not in provider._conversations


@pytest.mark.asyncio
async def test_clear_session(provider):
    """clear_session removes tracked conversation."""
    provider._conversations["s1"] = {"conv_id": "c1", "parent_msg_id": "p1"}
    provider._conversations["s2"] = {"conv_id": "c2", "parent_msg_id": "p2"}

    provider.clear_session("s1")
    assert "s1" not in provider._conversations
    assert "s2" in provider._conversations

    provider.clear_session()
    assert len(provider._conversations) == 0


# ---------------------------------------------------------------------------
# 6. Message conversion
# ---------------------------------------------------------------------------


class TestChatGPTMessageConversion:
    def setup_method(self):
        self.provider = ChatGPTWebProvider(default_model="gpt-4o")

    def test_system_and_user(self):
        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hello"},
        ]
        prompt, _ = self.provider._convert_messages(messages)
        assert "Be helpful." in prompt
        assert "[User]: Hello" in prompt

    def test_tool_defs_injected(self):
        messages = [
            {"role": "system", "content": "System."},
            {"role": "user", "content": "Do something."},
        ]
        tools = [{"type": "function", "function": {"name": "ping", "description": "Ping"}}]
        prompt, _ = self.provider._convert_messages(messages, tools)
        assert "ping" in prompt
        assert "Tool Use Instructions" in prompt

    def test_continuation_only_tool_results(self):
        messages = [
            {"role": "system", "content": "Sys."},
            {"role": "user", "content": "Do stuff"},
            {
                "role": "assistant", "content": "Calling...",
                "tool_calls": [
                    {"id": "a", "type": "function", "function": {"name": "t1", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "a", "name": "t1", "content": "result_a"},
        ]
        prompt = ChatGPTWebProvider._convert_continuation(messages)
        assert "result_a" in prompt
        assert "<tool_response" in prompt
        assert "Sys." not in prompt
        assert "Do stuff" not in prompt


# ---------------------------------------------------------------------------
# 7. Streaming
# ---------------------------------------------------------------------------


def _mock_client_streaming(provider: ChatGPTWebProvider, chunks: list[str],
                            conv_id: str = "gpt-conv-stream") -> None:
    """Patch client to simulate streaming."""
    provider._client.ensure_browser = AsyncMock()

    async def _fake_stream(prompt, model, conversation_id, parent_message_id, on_delta):
        full = ""
        for chunk in chunks:
            if on_delta:
                await on_delta(chunk)
            full += chunk
        return full, conv_id, "msg-stream"

    provider._client.send_message_stream = _fake_stream


@pytest.mark.asyncio
async def test_chat_stream_delivers_chunks(provider):
    """chat_stream calls on_content_delta for each chunk."""
    _mock_client_streaming(provider, ["Hello", ", from", " GPT!"])

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Say hello"},
    ]
    received: list[str] = []
    resp = await provider.chat_stream(messages, on_content_delta=lambda d: received.append(d))

    assert received == ["Hello", ", from", " GPT!"]
    assert resp.content == "Hello, from GPT!"
    assert resp.finish_reason == "stop"


@pytest.mark.asyncio
async def test_chat_stream_tool_call(provider):
    """Streaming tool call response parsed correctly."""
    chunks = [
        "I'll search.\n",
        '<tool_call id="c1" name="search">',
        '{"query": "Tokyo"}',
        "</tool_call>",
    ]
    _mock_client_streaming(provider, chunks)

    messages = [{"role": "user", "content": "Search Tokyo"}]
    resp = await provider.chat_stream(messages)

    assert resp.finish_reason == "tool_calls"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "search"

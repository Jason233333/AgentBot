"""Integration tests for the Gemini Web zero-token provider.

Browser layer is mocked so tests run without a real Chrome instance.
Gemini uses DOM simulation (no direct API access).
"""

import pytest
from unittest.mock import AsyncMock

from nanobot.providers.gemini_web_provider import GeminiWebProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def provider():
    """GeminiWebProvider with mocked browser client."""
    return GeminiWebProvider(default_model="gemini-2.5-pro")


def _mock_client(provider: GeminiWebProvider, response_text: str) -> None:
    """Patch the internal client to return a canned response."""
    provider._client.send_message = AsyncMock(return_value=response_text)
    provider._client.ensure_browser = AsyncMock()


# ---------------------------------------------------------------------------
# 1. Simple conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_chat(provider):
    """DOM simulation returns plain text → LLMResponse."""
    _mock_client(provider, "Hello from Gemini!")

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hi"},
    ]
    resp = await provider.chat(messages)

    assert resp.content == "Hello from Gemini!"
    assert resp.finish_reason == "stop"
    assert resp.tool_calls == []
    provider._client.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# 2. First message sends full history; new user message re-sends full history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_message_sends_full_prompt(provider):
    """First message in a new session sends the full prompt (system + history)."""
    _mock_client(provider, "ok")

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
    ]
    await provider.chat(messages, session_key="s1")

    sent_prompt = provider._client.send_message.call_args[0][0]
    assert "You are a helpful assistant." in sent_prompt
    assert "[User]: Hello" in sent_prompt


@pytest.mark.asyncio
async def test_new_user_message_in_existing_session_sends_only_last(provider):
    """After session exists, new user message sends only the last user message."""
    _mock_client(provider, "ok")
    provider._conversations["s1"] = True  # mark session as established

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Now do something else"},
    ]
    await provider.chat(messages, session_key="s1")

    sent_prompt = provider._client.send_message.call_args[0][0]
    assert "Now do something else" in sent_prompt
    # System prompt should NOT be re-sent for subsequent user messages
    assert "You are a helpful assistant." not in sent_prompt


# ---------------------------------------------------------------------------
# 3. Tool call response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_response(provider):
    """DOM returns XML tool_call → parsed ToolCallRequests."""
    web_response = (
        "I'll search.\n"
        '<tool_call id="tc1" name="web_search">{"query": "python"}</tool_call>'
    )
    _mock_client(provider, web_response)

    tools = [{
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    }]
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Search for python"},
    ]
    resp = await provider.chat(messages, tools=tools)

    assert resp.finish_reason == "tool_calls"
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "web_search"
    assert resp.tool_calls[0].arguments == {"query": "python"}


# ---------------------------------------------------------------------------
# 4. Tool result continuation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_result_continuation_sends_only_tool_result(provider):
    """Tool continuation sends only the tool result (not full history)."""
    _mock_client(provider, "Search results processed.")
    provider._conversations["s1"] = True

    messages = [
        {"role": "system", "content": "You are an assistant."},
        {"role": "user", "content": "Search python"},
        {
            "role": "assistant", "content": "Searching...",
            "tool_calls": [{
                "id": "tc1", "type": "function",
                "function": {"name": "web_search", "arguments": '{"query": "python"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "tc1", "name": "web_search", "content": "search results"},
    ]
    resp = await provider.chat(messages, session_key="s1")

    assert resp.content == "Search results processed."
    sent_prompt = provider._client.send_message.call_args[0][0]
    assert "<tool_response" in sent_prompt
    assert "search results" in sent_prompt
    # Full system + history should NOT be re-sent for tool continuation
    assert "You are an assistant." not in sent_prompt


# ---------------------------------------------------------------------------
# 5. Multiple tool calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_tool_calls(provider):
    web_response = (
        "Running.\n"
        '<tool_call id="t1" name="search">{"query": "a"}</tool_call>\n'
        '<tool_call id="t2" name="read">{"path": "b"}</tool_call>'
    )
    _mock_client(provider, web_response)

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
    resp = await provider.chat(messages)

    assert len(resp.tool_calls) == 2


# ---------------------------------------------------------------------------
# 6. Session management
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_message_sets_session_flag(provider):
    """After first message, session flag is set to True."""
    _mock_client(provider, "ok")

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
    ]
    await provider.chat(messages, session_key="s1")
    assert provider._conversations.get("s1") is True


@pytest.mark.asyncio
async def test_clear_session(provider):
    provider._conversations["s1"] = True
    provider._conversations["s2"] = True

    provider.clear_session("s1")
    assert "s1" not in provider._conversations
    assert "s2" in provider._conversations

    provider.clear_session()
    assert len(provider._conversations) == 0


# ---------------------------------------------------------------------------
# 7. Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_error_returns_error_response(provider):
    provider._client.send_message = AsyncMock(side_effect=ConnectionError("Chrome not running"))
    provider._client.ensure_browser = AsyncMock()

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    resp = await provider.chat(messages)

    assert resp.finish_reason == "error"
    assert "Chrome not running" in resp.content


@pytest.mark.asyncio
async def test_error_clears_session(provider):
    provider._conversations["s1"] = True
    provider._client.send_message = AsyncMock(side_effect=RuntimeError("DOM fail"))
    provider._client.ensure_browser = AsyncMock()

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "t", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "name": "t", "content": "r"},
    ]
    resp = await provider.chat(messages, session_key="s1")

    assert resp.finish_reason == "error"
    assert "s1" not in provider._conversations


# ---------------------------------------------------------------------------
# 8. Message conversion
# ---------------------------------------------------------------------------


class TestGeminiMessageConversion:
    def setup_method(self):
        self.provider = GeminiWebProvider(default_model="gemini-2.5-pro")

    def test_full_prompt_has_system_and_user(self):
        messages = [
            {"role": "system", "content": "Be helpful."},
            {"role": "user", "content": "Hello"},
        ]
        prompt = self.provider._build_full_prompt(messages)
        assert "Be helpful." in prompt
        assert "[User]: Hello" in prompt

    def test_full_prompt_includes_tool_defs(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "do it"},
        ]
        tools = [{"type": "function", "function": {"name": "ping", "description": "Ping"}}]
        prompt = self.provider._build_full_prompt(messages, tools)
        assert "ping" in prompt
        assert "Tool Use Instructions" in prompt

    def test_continuation_only_tool_results(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "ok", "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "t", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "a", "name": "t", "content": "result"},
        ]
        prompt = GeminiWebProvider._build_continuation_prompt(messages)
        assert "result" in prompt
        assert "<tool_response" in prompt
        assert "sys" not in prompt

    def test_last_user_message_only(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "last message"},
        ]
        prompt = self.provider._build_last_user_prompt(messages)
        assert "last message" in prompt
        assert "sys" not in prompt


# ---------------------------------------------------------------------------
# 9. Streaming
# ---------------------------------------------------------------------------


def _mock_client_streaming(provider: GeminiWebProvider, chunks: list[str]) -> None:
    provider._client.ensure_browser = AsyncMock()

    async def _fake_stream(prompt, on_delta=None):
        full = ""
        for chunk in chunks:
            if on_delta:
                await on_delta(chunk)
            full += chunk
        return full

    provider._client.send_message_stream = _fake_stream


@pytest.mark.asyncio
async def test_chat_stream_delivers_chunks(provider):
    _mock_client_streaming(provider, ["Hello", " from", " Gemini!"])

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Say hello"},
    ]
    received: list[str] = []
    resp = await provider.chat_stream(messages, on_content_delta=lambda d: received.append(d))

    assert received == ["Hello", " from", " Gemini!"]
    assert resp.content == "Hello from Gemini!"
    assert resp.finish_reason == "stop"


@pytest.mark.asyncio
async def test_chat_stream_tool_call(provider):
    chunks = [
        "Searching.\n",
        '<tool_call id="c1" name="search">{"query": "test"}</tool_call>',
    ]
    _mock_client_streaming(provider, chunks)

    messages = [{"role": "user", "content": "Search test"}]
    resp = await provider.chat_stream(messages)

    assert resp.finish_reason == "tool_calls"
    assert resp.tool_calls[0].name == "search"

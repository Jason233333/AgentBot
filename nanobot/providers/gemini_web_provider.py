"""Gemini Web (zero-token) LLM provider.

Conversation management (different from Claude/GPT — DOM-based):
- New session (session_key not in _conversations):
    Build full prompt (system + tools + history), send to Gemini DOM.
    Set _conversations[session_key] = True.
- Tool continuation (last msg is role="tool", session exists):
    Send only tool results (Gemini has prior context in page thread).
- New user message in existing session:
    Send only the last user message (Gemini has context from prior full prompt).

Message format: same XML tool format as ClaudeWebProvider.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.gemini_web_client import GeminiWebClient, GeminiWebClientConfig
from nanobot.providers.xml_tool_parser import (
    format_tool_definitions,
    format_tool_result,
    parse_tool_calls,
)


class GeminiWebProvider(LLMProvider):
    """LLM provider that uses gemini.google.com via DOM simulation.

    Three message modes:
    - New session: full prompt (system + tools + history)
    - Tool continuation: tool results only
    - Existing session, new user msg: last user message only
    """

    _MAX_HISTORY_TURNS = 10

    def __init__(
        self,
        cookie: str = "",
        user_agent: str = "",
        chrome_cdp_url: str = "http://127.0.0.1:9222",
        attach_only: bool = True,
        default_model: str = "gemini-2.5-pro",
        workspace: Path | str | None = None,
    ) -> None:
        super().__init__()
        self._default_model = default_model
        self._client = GeminiWebClient(GeminiWebClientConfig(
            cookie=cookie,
            user_agent=user_agent,
            chrome_cdp_url=chrome_cdp_url,
            attach_only=attach_only,
        ))
        # session_key → True (session established)
        self._conversations: dict[str, bool] = {}

    def get_default_model(self) -> str:
        return self._default_model

    def clear_session(self, key: str | None = None) -> None:
        if key and key in self._conversations:
            del self._conversations[key]
            logger.debug("[gemini-zero] cleared session {}", key)
        elif not key:
            self._conversations.clear()
            logger.debug("[gemini-zero] cleared all sessions")

    def _choose_prompt(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        session_key: str,
    ) -> str:
        """Choose prompt strategy based on session state."""
        has_session = self._conversations.get(session_key, False)
        is_continuation = (
            messages
            and messages[-1].get("role") == "tool"
            and has_session
        )

        if is_continuation:
            return self._build_continuation_prompt(messages)
        elif has_session:
            return self._build_last_user_prompt(messages)
        else:
            return self._build_full_prompt(messages, tools)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        session_key = kwargs.get("session_key", self._get_session_key(messages))
        has_session = self._conversations.get(session_key, False)

        try:
            prompt = self._choose_prompt(messages, tools, session_key)
            logger.info(
                "[gemini-zero] sending prompt ({} chars, session={})", len(prompt), has_session
            )

            response_text = await self._client.send_message(prompt)
            self._conversations[session_key] = True
            return self._parse_response(response_text)
        except Exception as exc:
            logger.exception("Gemini Web request failed")
            self._conversations.pop(session_key, None)
            return LLMResponse(
                content=f"Error calling Gemini Web: {exc}", finish_reason="error"
            )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        session_key = kwargs.get("session_key", self._get_session_key(messages))

        try:
            prompt = self._choose_prompt(messages, tools, session_key)

            async def _on_delta(delta: str) -> None:
                if on_content_delta:
                    import asyncio
                    result = on_content_delta(delta)
                    if asyncio.iscoroutine(result):
                        await result

            response_text = await self._client.send_message_stream(
                prompt=prompt, on_delta=_on_delta,
            )
            self._conversations[session_key] = True
            return self._parse_response(response_text)
        except Exception as exc:
            logger.exception("Gemini Web stream failed")
            self._conversations.pop(session_key, None)
            return LLMResponse(
                content=f"Error calling Gemini Web: {exc}", finish_reason="error"
            )

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_full_prompt(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        """Build full prompt: system + tools + history (for new sessions)."""
        parts: list[str] = []

        for msg in messages:
            if msg.get("role") == "system":
                parts.append(msg.get("content", ""))
                break

        parts.append(
            "IMPORTANT: You are the assistant. Never generate lines starting "
            "with '[User]:'. Only output your own response."
        )

        if tools:
            tool_text = format_tool_definitions(tools)
            if tool_text:
                parts.append(tool_text)

        history_msgs = [m for m in messages if m.get("role") != "system"]
        history_msgs = self._limit_history_turns(history_msgs, self._MAX_HISTORY_TURNS)

        for msg in history_msgs:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                text = content if isinstance(content, str) else (
                    " ".join(b.get("text", "") for b in content if isinstance(b, dict))
                    if isinstance(content, list) else str(content)
                )
                if text:
                    parts.append(f"[User]: {text}")
            elif role == "assistant":
                if content:
                    parts.append(f"[Assistant]: {content}")
                for tc in msg.get("tool_calls", []):
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "{}")
                    parts.append(
                        f'<tool_call id="{tc.get("id","")}" name="{fn.get("name","")}">'
                        f'{args}</tool_call>'
                    )
            elif role == "tool":
                result = content if isinstance(content, str) else (
                    "" if content is None else json.dumps(content, ensure_ascii=False)
                )
                parts.append(format_tool_result(
                    msg.get("tool_call_id", ""), msg.get("name", ""), result,
                ))

        return "\n\n".join(parts)

    @staticmethod
    def _build_continuation_prompt(messages: list[dict[str, Any]]) -> str:
        """Build incremental prompt with only new tool results."""
        last_assistant_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant" and messages[i].get("tool_calls"):
                last_assistant_idx = i
                break

        parts: list[str] = []
        start = last_assistant_idx + 1 if last_assistant_idx >= 0 else 0
        for msg in messages[start:]:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                result = content if isinstance(content, str) else (
                    "" if content is None else json.dumps(content, ensure_ascii=False)
                )
                parts.append(format_tool_result(
                    msg.get("tool_call_id", ""), msg.get("name", ""), result,
                ))
        return "\n\n".join(parts)

    @staticmethod
    def _build_last_user_prompt(messages: list[dict[str, Any]]) -> str:
        """Extract the last user message for existing sessions."""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
                return str(content)
        return ""

    def _parse_response(self, response_text: str) -> LLMResponse:
        if not response_text:
            return LLMResponse(content="", finish_reason="stop")
        clean_text, tool_calls = parse_tool_calls(response_text)
        if tool_calls:
            return LLMResponse(
                content=clean_text or None, tool_calls=tool_calls, finish_reason="tool_calls"
            )
        return LLMResponse(content=clean_text, finish_reason="stop")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _limit_history_turns(
        messages: list[dict[str, Any]], max_turns: int,
    ) -> list[dict[str, Any]]:
        if max_turns <= 0:
            return messages
        user_count = 0
        cut = 0
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                user_count += 1
                if user_count > max_turns:
                    cut = i + 1
                    break
        return messages[cut:]

    @staticmethod
    def _get_session_key(messages: list[dict[str, Any]]) -> str:
        for msg in messages:
            if msg.get("role") == "system":
                return str(hash(msg.get("content", "")[:200]))
        return "default"

    async def close(self) -> None:
        await self._client.close()

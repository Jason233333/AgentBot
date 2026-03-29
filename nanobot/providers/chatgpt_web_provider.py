"""ChatGPT Web (zero-token) LLM provider.

Mirrors ClaudeWebProvider's hybrid mode:
- New user message → new ChatGPT conversation (full prompt with system + history + tools)
- Tool continuation → reuse existing conversation (incremental tool results only)

Message format: same XML tool format as ClaudeWebProvider (reuses xml_tool_parser).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.chatgpt_web_client import ChatGPTWebClient, ChatGPTWebClientConfig
from nanobot.providers.xml_tool_parser import (
    format_tool_definitions,
    format_tool_result,
    parse_tool_calls,
)


class ChatGPTWebProvider(LLMProvider):
    """LLM provider that uses chatgpt.com Web interface via browser automation.

    Hybrid conversation management:
    - New user message → no conversation_id (new thread), send full prompt
    - Tool continuation (last msg role="tool", existing session) → reuse thread,
      send only tool results

    Conversation tracking: {session_key → {"conv_id": str, "parent_msg_id": str}}
    """

    _MAX_HISTORY_TURNS = 10

    def __init__(
        self,
        access_token: str = "",
        cookie: str = "",
        user_agent: str = "",
        chrome_cdp_url: str = "http://127.0.0.1:9222",
        attach_only: bool = True,
        default_model: str = "gpt-4o",
        workspace: Path | str | None = None,
    ) -> None:
        super().__init__()
        self._default_model = default_model
        self._client = ChatGPTWebClient(ChatGPTWebClientConfig(
            access_token=access_token,
            cookie=cookie,
            user_agent=user_agent,
            chrome_cdp_url=chrome_cdp_url,
            attach_only=attach_only,
        ))
        # session_key → {"conv_id": str | None, "parent_msg_id": str | None}
        self._conversations: dict[str, dict[str, str | None]] = {}

    def get_default_model(self) -> str:
        return self._default_model

    def clear_session(self, key: str | None = None) -> None:
        """Clear tracked conversation for the given session key."""
        if key and key in self._conversations:
            del self._conversations[key]
            logger.debug("[gpt-zero] cleared conversation for session {}", key)
        elif not key:
            self._conversations.clear()
            logger.debug("[gpt-zero] cleared all conversations")

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
        """Stream a response via ChatGPT Web."""
        model = model or self._default_model
        session_key = kwargs.get("session_key", self._get_session_key(messages))

        try:
            session = self._conversations.get(session_key)
            is_continuation = (
                messages
                and messages[-1].get("role") == "tool"
                and session is not None
            )

            if is_continuation:
                conv_id = session["conv_id"]
                parent_msg_id = session["parent_msg_id"]
                prompt = self._convert_continuation(messages)
            else:
                conv_id = None
                parent_msg_id = None
                prompt, _ = self._convert_messages(messages, tools)

            async def _on_delta(delta: str) -> None:
                if on_content_delta:
                    import asyncio
                    result = on_content_delta(delta)
                    if asyncio.iscoroutine(result):
                        await result

            gpt_model = ChatGPTWebClient._map_model(model)
            full_text, new_conv_id, new_parent_id = await self._client.send_message_stream(
                prompt=prompt,
                model=gpt_model,
                conversation_id=conv_id,
                parent_message_id=parent_msg_id,
                on_delta=_on_delta,
            )

            # Update conversation tracking.
            # When both IDs are absent the client used DOM fallback — clear session
            # so the next call always rebuilds the full prompt with complete context.
            if new_conv_id or new_parent_id:
                self._conversations[session_key] = {
                    "conv_id": new_conv_id or conv_id,
                    "parent_msg_id": new_parent_id or parent_msg_id,
                }
            else:
                self._conversations.pop(session_key, None)

            return self._parse_response(full_text)
        except Exception as exc:
            logger.exception("ChatGPT Web stream request failed")
            self._conversations.pop(session_key, None)
            return LLMResponse(content=f"Error calling ChatGPT Web: {exc}", finish_reason="error")

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
        """Send a chat request via ChatGPT Web."""
        model = model or self._default_model
        session_key = kwargs.get("session_key", self._get_session_key(messages))

        try:
            session = self._conversations.get(session_key)
            is_continuation = (
                messages
                and messages[-1].get("role") == "tool"
                and session is not None
            )

            if is_continuation:
                conv_id = session["conv_id"]
                parent_msg_id = session["parent_msg_id"]
                prompt = self._convert_continuation(messages)
                logger.info(
                    "[gpt-zero] continuation conv={} ({} chars)",
                    (conv_id or "")[:8] + "...", len(prompt),
                )
            else:
                conv_id = None
                parent_msg_id = None
                prompt, _ = self._convert_messages(messages, tools)
                logger.info("[gpt-zero] new conversation ({} chars)", len(prompt))

            gpt_model = ChatGPTWebClient._map_model(model)
            response_text, new_conv_id, new_parent_id = await self._client.send_message(
                prompt=prompt,
                model=gpt_model,
                conversation_id=conv_id,
                parent_message_id=parent_msg_id,
            )

            if new_conv_id or new_parent_id:
                self._conversations[session_key] = {
                    "conv_id": new_conv_id or conv_id,
                    "parent_msg_id": new_parent_id or parent_msg_id,
                }
            else:
                self._conversations.pop(session_key, None)

            return self._parse_response(response_text)
        except Exception as exc:
            logger.exception("ChatGPT Web request failed")
            self._conversations.pop(session_key, None)
            return LLMResponse(
                content=f"Error calling ChatGPT Web: {exc}",
                finish_reason="error",
            )

    # ------------------------------------------------------------------
    # Message conversion (reuses same XML format as ClaudeWebProvider)
    # ------------------------------------------------------------------

    def _convert_messages(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert messages to full XML prompt for ChatGPT Web."""
        parts: list[str] = []

        # 1. System message
        for msg in messages:
            if msg.get("role") == "system":
                parts.append(msg.get("content", ""))
                break

        parts.append(
            "IMPORTANT: You are the assistant. Never generate lines starting "
            "with '[User]:'. Only output your own response."
        )

        # 2. Tool definitions
        if tools:
            tool_text = format_tool_definitions(tools)
            if tool_text:
                parts.append(tool_text)

        # 3. History (trimmed)
        history_msgs = [m for m in messages if m.get("role") != "system"]
        history_msgs = self._limit_history_turns(history_msgs, self._MAX_HISTORY_TURNS)

        last_user_idx = -1
        for i in range(len(history_msgs) - 1, -1, -1):
            if history_msgs[i].get("role") == "user":
                last_user_idx = i
                break

        old_msgs = history_msgs[:last_user_idx] if last_user_idx > 0 else []
        current_msgs = history_msgs[last_user_idx:] if last_user_idx >= 0 else history_msgs

        for msg in old_msgs:
            role = msg.get("role", "")
            if role == "user":
                text = msg.get("content", "")
                if isinstance(text, list):
                    text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
                if text:
                    parts.append(f"[User]: {text}")
            elif role == "assistant":
                content = msg.get("content", "")
                if content:
                    cleaned = self._strip_tool_xml(content)
                    if cleaned:
                        parts.append(f"[Assistant]: {cleaned}")

        for msg in current_msgs:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                continue
            elif role == "user":
                text = content if isinstance(content, str) else " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                ) if isinstance(content, list) else str(content)
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
                    msg.get("tool_call_id", ""), msg.get("name", ""), result
                ))

        return "\n\n".join(parts), []

    @staticmethod
    def _convert_continuation(messages: list[dict[str, Any]]) -> str:
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
                    msg.get("tool_call_id", ""), msg.get("name", ""), result
                ))

        return "\n\n".join(parts)

    def _parse_response(self, response_text: str) -> LLMResponse:
        """Parse ChatGPT Web response text into LLMResponse."""
        if not response_text:
            return LLMResponse(content="", finish_reason="stop")

        clean_text, tool_calls = parse_tool_calls(response_text)
        if tool_calls:
            return LLMResponse(
                content=clean_text or None,
                tool_calls=tool_calls,
                finish_reason="tool_calls",
            )
        return LLMResponse(content=clean_text, finish_reason="stop")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @classmethod
    def _strip_tool_xml(cls, text: str) -> str:
        import re
        cleaned = re.sub(
            r"<tool_(?:response|call)\b[^>]*>[\s\S]*?</tool_(?:response|call)>",
            "", text, flags=re.IGNORECASE,
        )
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

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

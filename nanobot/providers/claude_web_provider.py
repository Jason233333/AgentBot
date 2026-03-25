"""Claude Web (zero-token) LLM provider.

Implements LLMProvider by routing requests through claude.ai Web interface
via Playwright browser automation. Converts between standard OpenAI-style
messages/tools and XML-based text format.

Hybrid mode: new user messages create a fresh conversation with the full
prompt (system + history + tools); tool continuations within the same
agent loop reuse the existing conversation with incremental messages.
This avoids context mixing from replayed history while keeping tool
round-trips efficient.
"""

from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.claude_web_client import ClaudeWebClient, ClaudeWebClientConfig
from nanobot.providers.xml_tool_parser import (
    format_tool_definitions,
    format_tool_result,
    parse_tool_calls,
)


class ClaudeWebProvider(LLMProvider):
    """LLM provider that uses claude.ai Web interface via browser automation.

    Hybrid conversation management:
    - New user message → create fresh conversation, send full prompt
    - Tool continuation (last msg is role="tool") → reuse conversation,
      send only incremental tool results

    This avoids context mixing (from replayed history in full prompts)
    while keeping tool round-trips efficient within the same agent loop.

    Session always stores standard JSON format. This provider handles:
    - Outbound: JSON messages + tool defs → XML-injected text prompt
    - Inbound: Plain text response → parsed LLMResponse with ToolCallRequests
    """

    # Max user turns to include in full prompt.
    # Keeps prompt focused on recent context; older history is discarded.
    _MAX_HISTORY_TURNS = 10

    def __init__(
        self,
        session_key: str = "",
        cookie: str = "",
        user_agent: str = "",
        organization_id: str = "",
        chrome_cdp_url: str = "http://127.0.0.1:9222",
        attach_only: bool = True,
        default_model: str = "claude-sonnet-4-6",
        workspace: Path | str | None = None,
    ) -> None:
        super().__init__()
        self._default_model = default_model
        self._client = ClaudeWebClient(ClaudeWebClientConfig(
            session_key=session_key,
            cookie=cookie,
            user_agent=user_agent,
            organization_id=organization_id,
            chrome_cdp_url=chrome_cdp_url,
            attach_only=attach_only,
        ))
        self._system_prompt_logged: bool = False
        # In-memory conversation tracking: session_key → conversation_id.
        # Used to reuse conversations for tool continuations.
        self._conversations: dict[str, str] = {}

    def get_default_model(self) -> str:
        """Get the default model for this provider."""
        return self._default_model

    def clear_session(self, key: str | None = None) -> None:
        """Clear tracked conversation for the given session key."""
        if key and key in self._conversations:
            del self._conversations[key]
            logger.debug("[zero-token] cleared conversation for session {}", key)
        elif not key:
            self._conversations.clear()
            logger.debug("[zero-token] cleared all conversations")

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
        """Send a chat request via Claude Web.

        Hybrid mode:
        - If last message is role="tool" and we have an existing conversation
          for this session → reuse conversation, send only tool results.
        - Otherwise → create a new conversation, send full prompt.
        """
        model = model or self._default_model
        session_key = kwargs.get("session_key", self._get_session_key(messages))

        try:
            is_continuation = (
                messages
                and messages[-1].get("role") == "tool"
                and session_key in self._conversations
            )

            if is_continuation:
                conv_id = self._conversations[session_key]
                prompt = self._convert_continuation(messages)
                attachments: list[dict[str, Any]] = []
                logger.info(
                    "[zero-token] continuation in conv={} ({} chars)",
                    conv_id[:8] + "...", len(prompt),
                )
            else:
                conv_id = await self._client.create_conversation(model)
                self._conversations[session_key] = conv_id
                prompt, attachments = self._convert_messages(messages, tools)
                logger.info(
                    "[zero-token] new conversation={} ({} chars)",
                    conv_id[:8] + "...", len(prompt),
                )

            logger.debug(
                "[zero-token] prompt ({} chars, conv={}):\n{}",
                len(prompt), conv_id[:8] + "...", prompt[:500],
            )
            response_text = await self._client.send_message(
                conversation_id=conv_id,
                prompt=prompt,
                model=model,
                attachments=attachments,
            )
            logger.debug(
                "[zero-token] response ({} chars):\n{}",
                len(response_text), response_text[:500] if response_text else "",
            )
            return self._parse_response(response_text)
        except Exception as exc:
            logger.exception("Claude Web request failed")
            # Clear conversation on error so next call creates a fresh one
            self._conversations.pop(session_key, None)
            error_msg = self._format_error(exc)
            return LLMResponse(
                content=error_msg,
                finish_reason="error",
            )

    # ------------------------------------------------------------------
    # Message conversion: JSON → XML prompt
    # ------------------------------------------------------------------

    def _convert_messages(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert standard messages list to a single prompt for Claude Web.

        Always builds a full prompt (system + tools + history) since each
        call uses a fresh conversation (stateless mode).

        Returns:
            Tuple of (prompt_text, attachments_list).
        """
        parts: list[str] = []
        attachments: list[dict[str, Any]] = []

        # Log full system prompt once on first call
        if not self._system_prompt_logged:
            for msg in messages:
                if msg.get("role") == "system":
                    sp = msg.get("content", "")
                    logger.info("[zero-token] system_prompt ({} chars):\n{}", len(sp), sp)
                    self._system_prompt_logged = True
                    break

        # Log message composition for debugging
        role_summary = [m.get("role", "?") for m in messages]
        logger.info(
            "[zero-token] _convert_messages: {} msgs, roles={}",
            len(messages), role_summary,
        )

        # 1. System message
        for msg in messages:
            if msg.get("role") == "system":
                parts.append(msg.get("content", ""))
                break

        # Guard against role-play: model must not fabricate user turns
        parts.append(
            "IMPORTANT: You are the assistant. Never generate lines starting "
            "with '[User]:'. Only output your own response. Wait for the real "
            "user to reply."
        )

        # 2. Inject tool definitions
        if tools:
            tool_text = format_tool_definitions(tools)
            if tool_text:
                parts.append(tool_text)

        # 3. History messages (skip system, already handled)
        # Limit history to last N user turns to avoid overly long prompts.
        history_msgs = [m for m in messages if m.get("role") != "system"]
        history_msgs = self._limit_history_turns(history_msgs, self._MAX_HISTORY_TURNS)

        # Split into "old turns" and "current turn" (last user msg + its tool calls).
        # Old turns: condensed (user + assistant text only, skip tool noise).
        # Current turn: full detail (tool_calls + tool_results preserved).
        last_user_idx = -1
        for i in range(len(history_msgs) - 1, -1, -1):
            if history_msgs[i].get("role") == "user":
                last_user_idx = i
                break

        old_msgs = history_msgs[:last_user_idx] if last_user_idx > 0 else []
        current_msgs = history_msgs[last_user_idx:] if last_user_idx >= 0 else history_msgs
        logger.info(
            "[zero-token] prompt split: {} old msgs (condensed), {} current msgs (full detail)",
            len(old_msgs), len(current_msgs),
        )

        # Old turns: condensed — skip tool_call/tool_result, keep user + assistant text only
        for msg in old_msgs:
            role = msg.get("role", "")
            if role == "user":
                text, msg_attachments = self._extract_user_content(msg)
                if text:
                    parts.append(f"[User]: {text}")
                attachments.extend(msg_attachments)
            elif role == "assistant":
                content = msg.get("content", "")
                if content:
                    content = self._strip_tool_xml(content)
                    if content:
                        parts.append(f"[Assistant]: {content}")
            # Skip role="tool" messages from old turns

        # Current turn: full detail (user + tool calls + tool results)
        for msg in current_msgs:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                continue
            elif role == "user":
                text, msg_attachments = self._extract_user_content(msg)
                if text:
                    parts.append(f"[User]: {text}")
                attachments.extend(msg_attachments)
            elif role == "assistant":
                if content:
                    parts.append(f"[Assistant]: {content}")
                # Reconstruct tool calls as XML (needed for current turn continuity)
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    tc_id = tc.get("id", "")
                    tc_name = fn.get("name", "")
                    tc_args = fn.get("arguments", "{}")
                    if isinstance(tc_args, str):
                        parts.append(
                            f'<tool_call id="{tc_id}" name="{tc_name}">{tc_args}</tool_call>'
                        )
                    else:
                        parts.append(
                            f'<tool_call id="{tc_id}" name="{tc_name}">'
                            f"{json.dumps(tc_args, ensure_ascii=False)}</tool_call>"
                        )
            elif role == "tool":
                tc_id = msg.get("tool_call_id", "")
                tc_name = msg.get("name", "")
                result = content if isinstance(content, str) else ("" if content is None else json.dumps(content, ensure_ascii=False))
                parts.append(format_tool_result(tc_id, tc_name, result))

        prompt = "\n\n".join(parts)
        return prompt, attachments

    @staticmethod
    def _convert_continuation(messages: list[dict[str, Any]]) -> str:
        """Build an incremental prompt containing only new tool results.

        Used when reusing an existing conversation for tool continuations.
        Only includes tool result messages that follow the last assistant
        message with tool_calls.
        """
        # Find the last assistant message with tool_calls
        last_assistant_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant" and messages[i].get("tool_calls"):
                last_assistant_idx = i
                break

        # Collect tool results after the last assistant tool_call message
        parts: list[str] = []
        start = last_assistant_idx + 1 if last_assistant_idx >= 0 else 0
        for msg in messages[start:]:
            if msg.get("role") == "tool":
                tc_id = msg.get("tool_call_id", "")
                tc_name = msg.get("name", "")
                content = msg.get("content", "")
                result = (
                    content if isinstance(content, str)
                    else ("" if content is None else json.dumps(content, ensure_ascii=False))
                )
                parts.append(format_tool_result(tc_id, tc_name, result))

        return "\n\n".join(parts)

    def _extract_user_content(
        self, msg: dict[str, Any]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Extract text and image attachments from a user message.

        Returns:
            Tuple of (text_content, attachments_list).
        """
        content = msg.get("content", "")
        attachments: list[dict[str, Any]] = []

        if isinstance(content, str):
            return content, []

        if isinstance(content, list):
            text_parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "image_url":
                    url = block.get("image_url", {}).get("url", "")
                    attachment = self._convert_image_to_attachment(url)
                    if attachment:
                        attachments.append(attachment)
            return "\n".join(text_parts), attachments

        return str(content), []

    @staticmethod
    def _convert_image_to_attachment(data_url: str) -> dict[str, Any] | None:
        """Convert a base64 data URL to claude.ai attachment format."""
        if not data_url.startswith("data:image/"):
            return None

        try:
            # Parse: data:image/png;base64,xxxxx
            header, b64_data = data_url.split(",", 1)
            mime_type = header.split(":")[1].split(";")[0]
            ext = mime_type.split("/")[1]
            raw = base64.b64decode(b64_data)

            return {
                "file_name": f"image.{ext}",
                "file_type": mime_type,
                "file_size": len(raw),
                "extracted_content": b64_data,
            }
        except Exception as exc:
            logger.warning("Failed to convert image attachment: {}", exc)
            return None

    # ------------------------------------------------------------------
    # Response parsing: text → LLMResponse
    # ------------------------------------------------------------------

    @staticmethod
    def _truncate_at_fake_user_turn(text: str) -> str:
        """Truncate response if the model starts role-playing user turns.

        Claude Web sometimes generates '[User]: ...' lines in its response,
        fabricating user messages. We cut the response at the first such line.
        """
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("[User]:") or line.startswith("[User]："):
                truncated = "\n".join(lines[:i]).rstrip()
                if truncated != text.rstrip():
                    logger.warning(
                        "[zero-token] truncated fake user turn from response at line {}: {}",
                        i, line[:80],
                    )
                return truncated
        return text

    def _parse_response(self, response_text: str) -> LLMResponse:
        """Parse Claude Web response text into a standard LLMResponse."""
        if not response_text:
            return LLMResponse(content="", finish_reason="stop")

        # Truncate if model fabricated user turns
        response_text = self._truncate_at_fake_user_turn(response_text)

        clean_text, tool_calls = parse_tool_calls(response_text)

        if tool_calls:
            for tc in tool_calls:
                logger.info(
                    "[zero-token] parsed tool call: {}({}) [id={}]",
                    tc.name,
                    json.dumps(tc.arguments, ensure_ascii=False),
                    tc.id,
                )
            if clean_text:
                logger.debug("[zero-token] clean text after stripping XML: {}", clean_text[:200])
            return LLMResponse(
                content=clean_text or None,
                tool_calls=tool_calls,
                finish_reason="tool_calls",
            )

        return LLMResponse(content=clean_text, finish_reason="stop")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    _RATE_LIMIT_RE = re.compile(r"HTTP 429.*?resetsAt\\*[\":]+(\\?\d+)", re.DOTALL)

    @classmethod
    def _format_error(cls, exc: Exception) -> str:
        """Format exception into a user-friendly error message.

        Parses claude.ai 429 rate limit errors to extract reset time.
        """
        msg = str(exc)
        match = cls._RATE_LIMIT_RE.search(msg)
        if match:
            try:
                ts_str = match.group(1).replace("\\", "")
                reset_ts = int(ts_str)
                reset_dt = datetime.fromtimestamp(reset_ts, tz=timezone.utc)
                remaining = reset_ts - int(time.time())
                if remaining > 0:
                    hours, rem = divmod(remaining, 3600)
                    minutes = rem // 60
                    time_str = f"{hours}h{minutes}m" if hours else f"{minutes}m"
                    return (
                        f"Claude Web rate limit exceeded. "
                        f"Resets at {reset_dt:%Y-%m-%d %H:%M UTC} (in ~{time_str})."
                    )
                return "Claude Web rate limit exceeded (should reset soon)."
            except (ValueError, OSError):
                pass
        return f"Error calling Claude Web: {exc}"

    _TOOL_XML_RE = re.compile(
        r"<tool_(?:response|call)\b[^>]*>[\s\S]*?</tool_(?:response|call)>",
        re.IGNORECASE,
    )

    @classmethod
    def _strip_tool_xml(cls, text: str) -> str:
        """Remove <tool_response> and <tool_call> XML blocks from text.

        Used to clean old assistant messages in history that have tool
        output embedded inline (e.g. from prior zero-token sessions).
        """
        cleaned = cls._TOOL_XML_RE.sub("", text)
        # Collapse multiple blank lines left after stripping
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned

    @staticmethod
    def _limit_history_turns(
        messages: list[dict[str, Any]], max_turns: int,
    ) -> list[dict[str, Any]]:
        """Keep only the last *max_turns* user turns and their associated messages.

        Scans backwards, counts user messages, and returns everything from the
        cut-off point onwards. This keeps assistant replies, tool calls, and
        tool results that belong to those turns.
        """
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

        if cut > 0:
            logger.debug(
                "[zero-token] history truncated: {} messages → {} (kept last {} user turns)",
                len(messages), len(messages) - cut, max_turns,
            )
        return messages[cut:]

    @staticmethod
    def _get_session_key(messages: list[dict[str, Any]]) -> str:
        """Derive a stable session key from the message list.

        Uses the system prompt hash as a rough session identifier.
        """
        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                return str(hash(content[:200]))
        return "default"

    async def close(self) -> None:
        """Close the browser client."""
        await self._client.close()

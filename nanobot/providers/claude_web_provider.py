"""Claude Web (zero-token) LLM provider.

Implements LLMProvider by routing requests through claude.ai Web interface
via Playwright browser automation. Converts between standard OpenAI-style
messages/tools and XML-based text format.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.providers.claude_web_client import ClaudeWebClient, ClaudeWebClientConfig
from nanobot.providers.xml_tool_parser import (
    format_tool_definitions,
    format_tool_result,
    parse_tool_calls,
)


class ClaudeWebProvider(LLMProvider):
    """LLM provider that uses claude.ai Web interface via browser automation.

    Session always stores standard JSON format. This provider handles:
    - Outbound: JSON messages + tool defs → XML-injected text prompt
    - Inbound: Plain text response → parsed LLMResponse with ToolCallRequests
    """

    # Max user turns to include in full prompt (new conversation).
    # Keeps prompt focused on recent context; older history is discarded.
    _MAX_HISTORY_TURNS = 10

    # After this many LLM round-trips in one claude.ai conversation,
    # rotate to a new conversation so the system prompt is re-injected.
    # claude.ai's context window will start dropping early content (including
    # the system prompt) after many turns, causing the model to "forget" its
    # identity and capabilities.
    _CONV_ROTATION_TURNS = 20

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
        # session_key (nanobot session) → conversation_id (claude.ai)
        # Persisted to disk so conversations survive restarts.
        self._conv_file: Path | None = None
        if workspace:
            self._conv_file = Path(workspace) / "sessions" / ".conversations.json"
        self._conversations: dict[str, str] = self._load_conversations()
        self._turn_counts: dict[str, int] = {}  # session_key → turn count
        self._system_prompt_logged: bool = False

    def get_default_model(self) -> str:
        """Get the default model for this provider."""
        return self._default_model

    def _load_conversations(self) -> dict[str, str]:
        """Load conversation mappings from disk."""
        if not self._conv_file or not self._conv_file.exists():
            return {}
        try:
            data = json.loads(self._conv_file.read_text(encoding="utf-8"))
            logger.info("[zero-token] loaded {} conversation mappings from disk", len(data))
            return data
        except Exception:
            logger.warning("[zero-token] failed to load conversations file, starting fresh")
            return {}

    def _save_conversations(self) -> None:
        """Persist conversation mappings to disk."""
        if not self._conv_file:
            return
        try:
            self._conv_file.parent.mkdir(parents=True, exist_ok=True)
            self._conv_file.write_text(
                json.dumps(self._conversations, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("[zero-token] failed to save conversations file")

    def clear_session(self, key: str | None = None) -> None:
        """Clear conversation mapping for a session (or all if key is None)."""
        if key is None:
            self._conversations.clear()
            logger.info("[zero-token] cleared all conversation mappings")
            self._turn_counts.clear()
        elif key in self._conversations:
            del self._conversations[key]
            self._turn_counts.pop(key, None)
            logger.info("[zero-token] cleared conversation for session {}", key)
        self._save_conversations()

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

        Converts standard messages/tools to XML prompt, sends through browser,
        parses response back to standard LLMResponse.
        """
        model = model or self._default_model

        # Get session key from kwargs (passed by AgentLoop), fallback to system prompt hash
        session_key = kwargs.get("session_key") or self._get_session_key(messages)
        logger.debug("[zero-token] session_key={}, conversation={}", session_key, self._conversations.get(session_key, "new"))

        try:
            return await self._chat_with_fallback(
                session_key, messages, tools, model,
            )
        except Exception as exc:
            logger.exception("Claude Web request failed")
            return LLMResponse(
                content=f"Error calling Claude Web: {exc}",
                finish_reason="error",
            )

    async def _chat_with_fallback(
        self,
        session_key: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
    ) -> LLMResponse:
        """Send message with automatic conversation rebuild on failure.

        If an existing conversation fails (SSE error, empty response),
        creates a new conversation and retries with full prompt.
        """
        conv_id = self._conversations.get(session_key)
        turn_count = self._turn_counts.get(session_key, 0)

        # Rotate conversation when turn count exceeds threshold.
        # This re-injects the system prompt so the model doesn't "forget"
        # its identity/tools after many turns in a long conversation.
        if conv_id is not None and turn_count >= self._CONV_ROTATION_TURNS:
            logger.info(
                "[zero-token] rotating conversation for session {} after {} turns",
                session_key, turn_count,
            )
            self._conversations.pop(session_key, None)
            self._turn_counts[session_key] = 0
            conv_id = None

        is_new_conversation = conv_id is None
        if is_new_conversation:
            conv_id = await self._client.create_conversation(model)
            self._conversations[session_key] = conv_id
            self._save_conversations()

        prompt, attachments = self._convert_messages(
            messages, tools, full_prompt=is_new_conversation,
        )

        try:
            logger.debug("[zero-token] prompt ({} chars, full={}):\n{}", len(prompt), is_new_conversation, prompt[:500])
            response_text = await self._client.send_message(
                conversation_id=conv_id,
                prompt=prompt,
                model=model,
                attachments=attachments,
            )
        except Exception:
            if is_new_conversation:
                raise  # No point rebuilding if this was already a fresh conversation
            logger.warning(
                "[zero-token] conversation {} failed, rebuilding with full prompt",
                conv_id[:8] + "...",
            )
            return await self._rebuild_conversation(
                session_key, messages, tools, model,
            )

        # Empty response from existing conversation → likely conversation lost
        if not response_text and not is_new_conversation:
            logger.warning(
                "[zero-token] empty response from conversation {}, rebuilding",
                conv_id[:8] + "...",
            )
            return await self._rebuild_conversation(
                session_key, messages, tools, model,
            )

        # Track turn count for conversation rotation
        self._turn_counts[session_key] = self._turn_counts.get(session_key, 0) + 1
        logger.debug("[zero-token] session {} turn count: {}/{}", session_key, self._turn_counts[session_key], self._CONV_ROTATION_TURNS)

        logger.debug("[zero-token] raw response ({} chars):\n{}", len(response_text), response_text)
        return self._parse_response(response_text)

    async def _rebuild_conversation(
        self,
        session_key: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
    ) -> LLMResponse:
        """Delete old conversation mapping, create new one, send full prompt."""
        # Remove stale mapping and reset turn counter
        self._conversations.pop(session_key, None)
        self._turn_counts[session_key] = 0

        # Create fresh conversation
        conv_id = await self._client.create_conversation(model)
        self._conversations[session_key] = conv_id
        self._save_conversations()
        logger.info("[zero-token] rebuilt conversation {} for session {}", conv_id[:8] + "...", session_key)

        # Send full prompt (all history + tools)
        prompt, attachments = self._convert_messages(
            messages, tools, full_prompt=True,
        )
        logger.debug("[zero-token] rebuild prompt ({} chars):\n{}", len(prompt), prompt[:500])
        response_text = await self._client.send_message(
            conversation_id=conv_id,
            prompt=prompt,
            model=model,
            attachments=attachments,
        )
        logger.debug("[zero-token] rebuild response ({} chars):\n{}", len(response_text), response_text)
        return self._parse_response(response_text)

    def clear_conversations(self) -> None:
        """Clear conversation mapping (e.g. on /new command)."""
        self._conversations.clear()

    # ------------------------------------------------------------------
    # Message conversion: JSON → XML prompt
    # ------------------------------------------------------------------

    def _convert_messages(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        full_prompt: bool = True,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert standard messages list to a single prompt for Claude Web.

        Args:
            messages: Standard message list.
            tools: Tool definitions.
            full_prompt: If True, send full prompt (system + history + tools).
                         If False, only send new content (existing conversation).

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

        # Check if this is a continuation: the LAST message(s) are tool results
        # from the current agent loop iteration (not historical tool results)
        is_continuation = (
            len(messages) > 0
            and messages[-1].get("role") == "tool"
        )

        # Log message composition for debugging
        role_summary = [m.get("role", "?") for m in messages]
        logger.info(
            "[zero-token] _convert_messages: {} msgs, roles={}, full_prompt={}, is_continuation={}",
            len(messages), role_summary, full_prompt, is_continuation,
        )

        # Build a tool reminder for continuation/existing conversation paths
        # so Claude doesn't "forget" it has tools after many turns.
        tool_hint = ""
        if tools and not full_prompt:
            tool_names = [
                (t.get("function", t) or {}).get("name", "?") for t in tools
            ]
            tool_hint = (
                '\n\n[SYSTEM HINT]: You have tools available: '
                + ", ".join(tool_names)
                + '. To use a tool, output: <tool_call id="unique_id" name="tool_name">{"param": "value"}</tool_call>'
            )

        # If continuation with tool results, only send the new tool results
        if is_continuation:
            logger.debug("[zero-token] continuation path: last message is tool result")
            prompt, atts = self._convert_continuation(messages)
            return prompt + tool_hint, atts

        # Existing conversation: only send the last user message
        if not full_prompt:
            logger.debug("[zero-token] existing conversation: sending only new user message")
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    text, msg_attachments = self._extract_user_content(msg)
                    return (text or "") + tool_hint, msg_attachments
            return tool_hint, []

        # New conversation: aggregate everything into a single prompt
        # 1. System message + tool definitions
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

        # Inject tool definitions
        if tools:
            tool_text = format_tool_definitions(tools)
            if tool_text:
                parts.append(tool_text)

        # 2. History messages (skip system, already handled)
        # Limit history to last N user turns to avoid overly long prompts
        # that confuse Claude about what's "current" vs "historical".
        history_msgs = [m for m in messages if m.get("role") != "system"]
        history_msgs = self._limit_history_turns(history_msgs, self._MAX_HISTORY_TURNS)

        # Split history into "old turns" and "current turn" (last user msg + its tool calls).
        # Old turns: only keep user messages + final assistant text (skip tool_call/tool_result noise).
        # Current turn: keep everything (user + assistant tool_calls + tool_results) so the
        # model can continue from where the agent loop left off.
        last_user_idx = -1
        for i in range(len(history_msgs) - 1, -1, -1):
            if history_msgs[i].get("role") == "user":
                last_user_idx = i
                break

        old_msgs = history_msgs[:last_user_idx] if last_user_idx > 0 else []
        current_msgs = history_msgs[last_user_idx:] if last_user_idx >= 0 else history_msgs
        logger.info(
            "[zero-token] full_prompt split: {} old msgs (condensed), {} current msgs (full detail)",
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
                # Only include assistant messages with actual text (skip tool-call-only messages)
                if content:
                    # Strip embedded <tool_response>/<tool_call> XML from old assistant content
                    # (these can appear when history was saved with tool output inline)
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

    def _convert_continuation(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert continuation messages (tool results) to prompt text.

        Only sends new tool results since the last assistant message.
        """
        parts: list[str] = []
        attachments: list[dict[str, Any]] = []

        # Find the last assistant message with tool_calls, then collect tool results after it
        last_assistant_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant" and messages[i].get("tool_calls"):
                last_assistant_idx = i
                break

        if last_assistant_idx < 0:
            # No tool calls found, just send the last user message
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    text, msg_attachments = self._extract_user_content(msg)
                    return text or "", msg_attachments
            return "", []

        # Collect tool results after the last assistant tool_call
        for msg in messages[last_assistant_idx + 1:]:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "tool":
                tc_id = msg.get("tool_call_id", "")
                tc_name = msg.get("name", "")
                result = content if isinstance(content, str) else ("" if content is None else json.dumps(content, ensure_ascii=False))
                parts.append(format_tool_result(tc_id, tc_name, result))
            elif role == "user":
                text, msg_attachments = self._extract_user_content(msg)
                if text:
                    parts.append(text)
                attachments.extend(msg_attachments)

        if parts:
            parts.append("\nPlease proceed based on these tool results.")

        return "\n\n".join(parts), attachments

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

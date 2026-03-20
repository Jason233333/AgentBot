"""Claude Web (zero-token) LLM provider.

Implements LLMProvider by routing requests through claude.ai Web interface
via Playwright browser automation. Converts between standard OpenAI-style
messages/tools and XML-based text format.
"""

from __future__ import annotations

import base64
import json
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

    def __init__(
        self,
        session_key: str = "",
        cookie: str = "",
        user_agent: str = "",
        organization_id: str = "",
        chrome_cdp_url: str = "http://127.0.0.1:9222",
        attach_only: bool = True,
        default_model: str = "claude-sonnet-4-6",
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
        self._conversations: dict[str, str] = {}

    def get_default_model(self) -> str:
        """Get the default model for this provider."""
        return self._default_model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Send a chat request via Claude Web.

        Converts standard messages/tools to XML prompt, sends through browser,
        parses response back to standard LLMResponse.
        """
        model = model or self._default_model

        # Determine session key from messages (use a hash or fixed key)
        session_key = self._get_session_key(messages)

        # Convert messages to prompt text
        prompt, attachments = self._convert_messages(messages, tools)

        try:
            # Get or create conversation
            conv_id = self._conversations.get(session_key)
            if not conv_id:
                conv_id = await self._client.create_conversation(model)
                self._conversations[session_key] = conv_id

            # Send message
            response_text = await self._client.send_message(
                conversation_id=conv_id,
                prompt=prompt,
                model=model,
                attachments=attachments,
            )

            # Parse response
            return self._parse_response(response_text)

        except Exception as exc:
            logger.exception("Claude Web request failed")
            return LLMResponse(
                content=f"Error calling Claude Web: {exc}",
                finish_reason="error",
            )

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
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert standard messages list to a single prompt for Claude Web.

        Returns:
            Tuple of (prompt_text, attachments_list).
        """
        parts: list[str] = []
        attachments: list[dict[str, Any]] = []

        # Check if this is a continuation (has tool results)
        has_tool_results = any(m.get("role") == "tool" for m in messages)

        # If continuation with tool results, only send the new tool results
        if has_tool_results:
            return self._convert_continuation(messages)

        # First turn: aggregate everything into a single prompt
        # 1. System message + tool definitions
        for msg in messages:
            if msg.get("role") == "system":
                parts.append(msg.get("content", ""))
                break

        # Inject tool definitions
        if tools:
            tool_text = format_tool_definitions(tools)
            if tool_text:
                parts.append(tool_text)

        # 2. History messages (skip system, already handled)
        for msg in messages:
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
                # Reconstruct tool calls as XML in history
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
                # Tool results in history
                tc_id = msg.get("tool_call_id", "")
                tc_name = msg.get("name", "")
                result = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
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
                result = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
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
        except Exception:
            logger.warning("Failed to convert image attachment")
            return None

    # ------------------------------------------------------------------
    # Response parsing: text → LLMResponse
    # ------------------------------------------------------------------

    def _parse_response(self, response_text: str) -> LLMResponse:
        """Parse Claude Web response text into a standard LLMResponse."""
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

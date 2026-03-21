"""XML tool call parser for zero-token (Claude Web) provider.

Handles bidirectional conversion between OpenAI-style tool definitions/calls
and XML-based text format used in Claude Web prompt injection.
"""

import json
import re
import string
import random
from typing import Any

from loguru import logger

from nanobot.providers.base import ToolCallRequest


# ---------------------------------------------------------------------------
# Regex for extracting <tool_call> tags from LLM response text
# ---------------------------------------------------------------------------

TOOL_CALL_RE = re.compile(
    r"<tool_call\s+"
    r'id=["\']?(?P<id>[^"\'>\s]+)["\']?\s+'
    r'name=["\']?(?P<name>[^"\'>\s]+)["\']?\s*>'
    r"(?P<body>[\s\S]*?)"
    r"</tool_call>",
    re.IGNORECASE,
)

# Fallback: name before id
TOOL_CALL_RE_ALT = re.compile(
    r"<tool_call\s+"
    r'name=["\']?(?P<name>[^"\'>\s]+)["\']?\s+'
    r'id=["\']?(?P<id>[^"\'>\s]+)["\']?\s*>'
    r"(?P<body>[\s\S]*?)"
    r"</tool_call>",
    re.IGNORECASE,
)

# Strip markdown code fences from JSON body
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?|\n?```\s*$", re.MULTILINE)


def _short_id(length: int = 9) -> str:
    """Generate a short alphanumeric ID."""
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


# ---------------------------------------------------------------------------
# Tool definitions → prompt text
# ---------------------------------------------------------------------------


def format_tool_definitions(tools: list[dict[str, Any]]) -> str:
    """Convert OpenAI-style tool definitions to prompt text for injection.

    Args:
        tools: List of tool defs in OpenAI format, each with
               {"type": "function", "function": {"name", "description", "parameters"}}.

    Returns:
        Formatted text block to prepend to the system/user prompt.
    """
    if not tools:
        return ""

    lines = [
        "## Tool Use Instructions",
        "",
        "You have access to external tools. You MUST use them when appropriate.",
        "To call a tool, output the following XML tag — this is the ONLY way to invoke tools:",
        "",
        '<tool_call id="unique_id" name="tool_name">{"param": "value"}</tool_call>',
        "",
        "Rules:",
        "- ALWAYS use the exact XML format above. Do NOT describe tool usage in plain text.",
        "- Generate a unique id for each call (e.g. call_1, call_2).",
        "- The body MUST be valid JSON matching the tool's parameters.",
        "- You may call multiple tools in a single response.",
        "- When the user's request requires information you don't have, USE A TOOL.",
        "",
        "### Example",
        "",
        "User: What's the weather in Tokyo?",
        'Assistant: I\'ll check the weather for you.',
        '<tool_call id="call_1" name="get_weather">{"city": "Tokyo"}</tool_call>',
        "",
        "User: Read main.py and search for errors",
        "Assistant: I'll read the file and search.",
        '<tool_call id="call_1" name="read_file">{"path": "main.py"}</tool_call>',
        '<tool_call id="call_2" name="web_search">{"query": "common python errors"}</tool_call>',
        "",
        "### Available Tools",
        "",
    ]

    for tool in tools:
        fn = tool.get("function", tool)
        name = fn.get("name", "unknown")
        desc = fn.get("description", "")
        params = fn.get("parameters", {})

        lines.append(f"#### {name}")
        if desc:
            lines.append(desc)
        if params:
            lines.append(f"Parameters: {json.dumps(params, ensure_ascii=False)}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Response text → ToolCallRequest list
# ---------------------------------------------------------------------------


def parse_tool_calls(text: str) -> tuple[str, list[ToolCallRequest]]:
    """Extract tool calls from LLM response text containing <tool_call> XML tags.

    Args:
        text: Raw response text from Claude Web.

    Returns:
        Tuple of (clean_text_without_tags, list_of_tool_call_requests).
    """
    if not text:
        return "", []

    matches = list(TOOL_CALL_RE.finditer(text))
    if not matches:
        matches = list(TOOL_CALL_RE_ALT.finditer(text))
    if not matches:
        return text, []

    logger.debug("[xml-parser] found {} <tool_call> tag(s) in response", len(matches))

    tool_calls: list[ToolCallRequest] = []
    for m in matches:
        call_id = m.group("id") or _short_id()
        name = m.group("name")
        body = m.group("body").strip()

        logger.debug("[xml-parser] raw XML: <tool_call id=\"{}\" name=\"{}\">{}</tool_call>", call_id, name, body[:200])

        # Strip markdown code fences
        body = _CODE_FENCE_RE.sub("", body).strip()

        # Parse JSON arguments
        try:
            arguments = json.loads(body) if body else {}
        except json.JSONDecodeError:
            logger.warning("[xml-parser] JSON parse failed for {}, trying json_repair: {}", name, body[:100])
            # Try json_repair as fallback
            try:
                import json_repair
                arguments = json_repair.loads(body)
            except Exception:
                arguments = {"_raw": body}

        if not isinstance(arguments, dict):
            arguments = {"_raw": arguments}

        tool_calls.append(ToolCallRequest(
            id=call_id,
            name=name,
            arguments=arguments,
        ))

    # Remove tool_call tags from text to get clean content
    clean = TOOL_CALL_RE.sub("", text)
    clean = TOOL_CALL_RE_ALT.sub("", clean)
    clean = clean.strip()

    return clean, tool_calls


# ---------------------------------------------------------------------------
# Tool result → XML text (for sending back to Claude Web)
# ---------------------------------------------------------------------------


def format_tool_result(tool_call_id: str, tool_name: str, result: str) -> str:
    """Format a tool execution result as XML for sending back to Claude Web.

    Args:
        tool_call_id: The ID from the original tool call.
        tool_name: The name of the tool.
        result: The tool execution result text.

    Returns:
        XML-formatted tool response string.
    """
    return (
        f'<tool_response id="{tool_call_id}" name="{tool_name}">\n'
        f"{result}\n"
        f"</tool_response>"
    )

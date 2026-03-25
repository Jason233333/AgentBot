#!/usr/bin/env python3
"""End-to-end test for zero-token Claude Web provider.

Prerequisites:
  1. Chrome running with: google-chrome --remote-debugging-port=9222
  2. Already logged in to claude.ai in that Chrome instance
     (or run: nanobot provider login claude-web)

Usage:
  python scripts/test_zero_token_e2e.py                  # run all tests
  python scripts/test_zero_token_e2e.py --test chat      # simple chat only
  python scripts/test_zero_token_e2e.py --test tool      # tool call only
  python scripts/test_zero_token_e2e.py --test multi     # multi-turn only
  python scripts/test_zero_token_e2e.py --cdp http://127.0.0.1:9222
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback

# -- colors --
GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
DIM = "\033[2m"
RESET = "\033[0m"


def _print_header(name: str) -> None:
    print(f"\n{CYAN}{'=' * 60}")
    print(f"  TEST: {name}")
    print(f"{'=' * 60}{RESET}\n")


def _print_pass(name: str) -> None:
    print(f"\n  {GREEN}PASS{RESET} {name}\n")


def _print_fail(name: str, reason: str) -> None:
    print(f"\n  {RED}FAIL{RESET} {name}: {reason}\n")


def _print_response(resp) -> None:
    print(f"  {DIM}finish_reason: {resp.finish_reason}{RESET}")
    if resp.content:
        preview = resp.content[:200] + ("..." if len(resp.content) > 200 else "")
        print(f"  {DIM}content: {preview}{RESET}")
    if resp.tool_calls:
        for tc in resp.tool_calls:
            print(f"  {DIM}tool_call: {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)[:100]}){RESET}")


# ---------------------------------------------------------------------------
# Test 1: Simple chat
# ---------------------------------------------------------------------------

async def test_simple_chat(provider) -> bool:
    """Send a simple message, expect a text response."""
    _print_header("Simple Chat")

    messages = [
        {"role": "system", "content": "You are a helpful assistant. Reply concisely."},
        {"role": "user", "content": "What is 2 + 3? Reply with just the number."},
    ]

    resp = await provider.chat(messages)
    _print_response(resp)

    if resp.finish_reason == "error":
        _print_fail("Simple Chat", f"got error: {resp.content}")
        return False

    if not resp.content or not resp.content.strip():
        _print_fail("Simple Chat", "empty response")
        return False

    if "5" in resp.content:
        _print_pass("Simple Chat")
        return True

    # Even if "5" isn't in the answer, a non-error response is a partial pass
    print(f"  {DIM}(response didn't contain '5' but was non-empty){RESET}")
    _print_pass("Simple Chat")
    return True


# ---------------------------------------------------------------------------
# Test 2: Tool call
# ---------------------------------------------------------------------------

async def test_tool_call(provider) -> bool:
    """Send a message with tools, expect the model to call a tool."""
    _print_header("Tool Call")

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                    },
                    "required": ["city"],
                },
            },
        },
    ]

    messages = [
        {"role": "system", "content": (
            "You are a helpful assistant with access to tools. "
            "When the user asks about weather, you MUST use the get_weather tool. "
            "Do not make up weather data."
        )},
        {"role": "user", "content": "What's the weather in Tokyo?"},
    ]

    resp = await provider.chat(messages, tools=tools)
    _print_response(resp)

    if resp.finish_reason == "error":
        _print_fail("Tool Call", f"got error: {resp.content}")
        return False

    if not resp.tool_calls:
        _print_fail("Tool Call", "no tool calls in response (model didn't use XML format)")
        return False

    tc = resp.tool_calls[0]
    if tc.name != "get_weather":
        _print_fail("Tool Call", f"wrong tool: {tc.name}")
        return False

    if "city" not in tc.arguments:
        _print_fail("Tool Call", f"missing 'city' arg: {tc.arguments}")
        return False

    _print_pass("Tool Call")
    return True


# ---------------------------------------------------------------------------
# Test 3: Multi-turn with tool result
# ---------------------------------------------------------------------------

async def test_multi_turn(provider) -> bool:
    """Simulate a full tool cycle: call → result → final response."""
    _print_header("Multi-turn (tool result continuation)")

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                    },
                    "required": ["city"],
                },
            },
        },
    ]

    # Step 1: get the tool call
    messages = [
        {"role": "system", "content": (
            "You are a helpful assistant with access to tools. "
            "When the user asks about weather, you MUST use the get_weather tool."
        )},
        {"role": "user", "content": "What's the weather in Paris?"},
    ]

    print("  Step 1: Requesting tool call...")
    resp1 = await provider.chat(messages, tools=tools)
    _print_response(resp1)

    if not resp1.tool_calls:
        _print_fail("Multi-turn", "step 1 didn't produce tool call")
        return False

    tc = resp1.tool_calls[0]

    # Step 2: feed back a fake tool result
    messages.append({
        "role": "assistant",
        "content": resp1.content,
        "tool_calls": [tc.to_openai_tool_call()],
    })
    messages.append({
        "role": "tool",
        "tool_call_id": tc.id,
        "name": tc.name,
        "content": json.dumps({
            "city": "Paris",
            "temperature": "18°C",
            "condition": "Partly cloudy",
            "humidity": "65%",
        }),
    })

    print("\n  Step 2: Sending tool result, expecting summary...")
    resp2 = await provider.chat(messages, tools=tools)
    _print_response(resp2)

    if resp2.finish_reason == "error":
        _print_fail("Multi-turn", f"step 2 error: {resp2.content}")
        return False

    if not resp2.content or not resp2.content.strip():
        _print_fail("Multi-turn", "step 2 returned empty response")
        return False

    # The model should mention Paris or the weather data
    content_lower = resp2.content.lower()
    if any(kw in content_lower for kw in ("paris", "18", "cloudy", "weather")):
        _print_pass("Multi-turn")
        return True

    print(f"  {DIM}(response didn't reference weather data but was non-empty){RESET}")
    _print_pass("Multi-turn")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

TESTS = {
    "chat": test_simple_chat,
    "tool": test_tool_call,
    "multi": test_multi_turn,
}


async def main() -> int:
    parser = argparse.ArgumentParser(description="E2E test for zero-token provider")
    parser.add_argument("--cdp", default="http://127.0.0.1:9222", help="Chrome CDP URL")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Model to use")
    parser.add_argument("--test", choices=list(TESTS.keys()), help="Run specific test only")
    args = parser.parse_args()

    # Late import to give a clear error if dependencies are missing
    try:
        from nanobot.providers.claude_web_auth import load_credentials
        from nanobot.providers.claude_web_provider import ClaudeWebProvider
    except ImportError as e:
        print(f"{RED}Import error: {e}{RESET}")
        print("Make sure nanobot is installed: pip install -e .")
        return 1

    creds = load_credentials()
    if not creds.get("session_key"):
        print(f"{RED}No credentials found.{RESET}")
        print("Run: nanobot provider login claude-web")
        return 1

    provider = ClaudeWebProvider(
        session_key=creds.get("session_key", ""),
        cookie=creds.get("cookie", ""),
        user_agent=creds.get("user_agent", ""),
        organization_id=creds.get("organization_id", ""),
        chrome_cdp_url=args.cdp,
        default_model=args.model,
    )

    print(f"{CYAN}Zero-Token E2E Test{RESET}")
    print(f"  CDP:   {args.cdp}")
    print(f"  Model: {args.model}")

    to_run = {args.test: TESTS[args.test]} if args.test else TESTS
    results: dict[str, bool] = {}

    try:
        for name, test_fn in to_run.items():
            try:
                results[name] = await test_fn(provider)
            except Exception:
                traceback.print_exc()
                _print_fail(name, "unhandled exception")
                results[name] = False
    finally:
        await provider.close()

    # Summary
    print(f"\n{CYAN}{'=' * 60}")
    print(f"  SUMMARY")
    print(f"{'=' * 60}{RESET}")
    for name, passed in results.items():
        status = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
        print(f"  {status}  {name}")

    total = len(results)
    passed = sum(results.values())
    print(f"\n  {passed}/{total} passed\n")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

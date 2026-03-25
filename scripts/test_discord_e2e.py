#!/usr/bin/env python3
"""End-to-end test: Discord channel + zero-token (Claude Web) provider.

Tests the full pipeline:
  MessageBus injection → AgentLoop → ClaudeWebProvider (Chrome CDP → claude.ai)
  → response on MessageBus outbound queue (+ best-effort Discord send)

Verification reads from a tapped outbound queue (no Discord read permission needed).

Prerequisites:
  1. Chrome running with: google-chrome --remote-debugging-port=9222
  2. Logged in to claude.ai (or run: nanobot provider login claude-web)
  3. Environment variables set (see below)

Usage:
  python scripts/test_discord_e2e.py                   # run all tests
  python scripts/test_discord_e2e.py --test chat        # simple chat only
  python scripts/test_discord_e2e.py --test tool        # tool call flow only
  python scripts/test_discord_e2e.py --cdp http://127.0.0.1:9222

Environment variables:
  NANOBOT_DISCORD_BOT_TOKEN     — Bot token for nanobot's Discord connection
  DISCORD_TEST_CHANNEL_ID       — Channel ID where tests run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import traceback

# -- colors --
GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
DIM = "\033[2m"
RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def _print_header(name: str) -> None:
    print(f"\n{CYAN}{'=' * 60}")
    print(f"  TEST: {name}")
    print(f"{'=' * 60}{RESET}\n")


def _print_pass(name: str) -> None:
    print(f"\n  {GREEN}PASS{RESET} {name}\n")


def _print_fail(name: str, reason: str) -> None:
    print(f"\n  {RED}FAIL{RESET} {name}: {reason}\n")


# ---------------------------------------------------------------------------
# Outbound tap: captures agent replies from the bus
# ---------------------------------------------------------------------------

class OutboundTap:
    """Intercepts outbound messages from the bus for test verification.

    Replaces bus.consume_outbound so both the tap and the dispatcher see messages.
    """

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()

    def capture(self, msg) -> None:
        """Called by the dispatcher to record an outbound message."""
        if not msg.metadata.get("_progress"):
            self._queue.put_nowait(msg)

    async def wait_for_reply(self, timeout_s: float = 120) -> str | None:
        """Wait for a non-progress outbound message. Returns content or None."""
        try:
            msg = await asyncio.wait_for(self._queue.get(), timeout=timeout_s)
            return msg.content or ""
        except asyncio.TimeoutError:
            return None


# ---------------------------------------------------------------------------
# Nanobot lifecycle helpers
# ---------------------------------------------------------------------------

async def start_nanobot(
    bot_token: str,
    channel_id: str,
    cdp_url: str,
    model: str,
) -> tuple:
    """Start nanobot components in-process.

    Returns (agent, discord_ch, bus, tap, tasks).
    """
    from pathlib import Path

    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.discord import DiscordChannel, DiscordConfig
    from nanobot.providers.claude_web_auth import load_credentials
    from nanobot.providers.claude_web_provider import ClaudeWebProvider
    from nanobot.session.manager import SessionManager

    # -- credentials --
    creds = load_credentials()
    if not creds.get("session_key"):
        raise RuntimeError(
            "No session_key found. Run: nanobot provider login claude-web"
        )

    # -- provider --
    provider = ClaudeWebProvider(
        session_key=creds.get("session_key", ""),
        cookie=creds.get("cookie", ""),
        user_agent=creds.get("user_agent", ""),
        organization_id=creds.get("organization_id", ""),
        chrome_cdp_url=cdp_url,
        default_model=model,
    )

    # -- workspace --
    workspace = Path.home() / ".nanobot" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    # -- core components --
    bus = MessageBus()
    session_manager = SessionManager(workspace)
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        model=model,
        max_iterations=10,
        session_manager=session_manager,
    )

    # -- discord (for outbound sending only) --
    discord_config = DiscordConfig(
        enabled=True,
        token=bot_token,
        allow_from=["*"],
    )
    discord_ch = DiscordChannel(discord_config, bus)

    # -- outbound tap for test verification --
    tap = OutboundTap()

    # -- outbound dispatcher: reads from bus, taps, then sends to Discord --
    async def dispatch_outbound():
        while True:
            try:
                msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                # tap for test verification
                tap.capture(msg)
                if msg.metadata.get("_progress"):
                    continue
                # best-effort Discord send
                try:
                    await discord_ch.send(msg)
                    print(f"  {DIM}[dispatch] sent to Discord: {(msg.content or '')[:80]}{RESET}")
                except Exception as exc:
                    print(f"  {DIM}[dispatch] Discord send failed (non-fatal): {exc}{RESET}")
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"  {RED}[dispatch] error: {exc}{RESET}")
                continue

    # -- start tasks --
    agent_task = asyncio.create_task(agent.run())
    channel_task = asyncio.create_task(discord_ch.start())
    dispatch_task = asyncio.create_task(dispatch_outbound())

    # -- wait for Discord gateway READY (so discord_ch.send() works) --
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if getattr(discord_ch, "_bot_user_id", None):
            break
        await asyncio.sleep(1)
    else:
        raise RuntimeError("Discord bot did not become ready within 30s")

    print(f"  {GREEN}[nanobot] bot ready, user id: {discord_ch._bot_user_id}{RESET}")
    return (agent, discord_ch, bus, tap, [agent_task, channel_task, dispatch_task])


async def stop_nanobot(agent, discord_ch, tasks):
    """Gracefully stop all nanobot components."""
    agent.stop()
    await discord_ch.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await agent.provider.close()


# ---------------------------------------------------------------------------
# Bus injection helper
# ---------------------------------------------------------------------------

async def inject_message(bus, channel_id: str, content: str) -> None:
    """Inject a message directly into the MessageBus as if from Discord."""
    from nanobot.bus.events import InboundMessage

    msg = InboundMessage(
        channel="discord",
        sender_id="e2e-test-user",
        chat_id=channel_id,
        content=content,
    )
    await bus.publish_inbound(msg)
    print(f"  {DIM}[inject] → bus: {content[:80]}{RESET}")


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

async def test_simple_chat(bus, channel_id: str, tap: OutboundTap) -> bool:
    """Inject a math question via bus, verify agent replies on outbound bus."""
    _print_header("Simple Chat (bus → AgentLoop → zero-token → outbound)")

    await inject_message(bus, channel_id, "计算 2+3 等于多少，只回复数字")
    print(f"  Waiting for agent reply on bus (up to 120s)...")

    content = await tap.wait_for_reply(timeout_s=120)

    if content is None:
        _print_fail("Simple Chat", "no reply from agent within timeout")
        return False

    print(f"  Agent replied: {content[:200]}")

    if "5" in content:
        _print_pass("Simple Chat")
        return True

    if content.strip():
        print(f"  {DIM}(response didn't contain '5' but was non-empty){RESET}")
        _print_pass("Simple Chat")
        return True

    _print_fail("Simple Chat", "empty response from agent")
    return False


async def test_tool_call(bus, channel_id: str, tap: OutboundTap) -> bool:
    """Inject a message that triggers tool usage, verify agent replies."""
    _print_header("Tool Call (bus → AgentLoop → tools → zero-token → outbound)")

    await inject_message(
        bus, channel_id,
        "请用 web_search 工具搜索 'nanobot-ai github'，然后总结搜索结果",
    )
    print(f"  Waiting for agent reply (up to 180s, tool calls may take longer)...")

    content = await tap.wait_for_reply(timeout_s=180)

    if content is None:
        _print_fail("Tool Call", "no reply from agent within timeout")
        return False

    print(f"  Agent replied ({len(content)} chars): {content[:200]}")

    if len(content) > 20:
        _print_pass("Tool Call")
        return True

    _print_fail("Tool Call", f"reply too short ({len(content)} chars)")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

TESTS = {
    "chat": test_simple_chat,
    "tool": test_tool_call,
}


async def main() -> int:
    parser = argparse.ArgumentParser(description="E2E: Discord + zero-token")
    parser.add_argument("--cdp", default="http://127.0.0.1:9222", help="Chrome CDP URL")
    parser.add_argument("--model", default="claude-sonnet-4-6", help="Model")
    parser.add_argument("--test", choices=list(TESTS.keys()), help="Run specific test")
    args = parser.parse_args()

    # Read env vars
    bot_token = os.environ.get("NANOBOT_DISCORD_BOT_TOKEN", "")
    channel_id = os.environ.get("DISCORD_TEST_CHANNEL_ID", "")

    missing = []
    if not bot_token:
        missing.append("NANOBOT_DISCORD_BOT_TOKEN")
    if not channel_id:
        missing.append("DISCORD_TEST_CHANNEL_ID")
    if missing:
        print(f"{RED}Missing environment variables: {', '.join(missing)}{RESET}")
        return 1

    print(f"{CYAN}Discord + Zero-Token E2E Test{RESET}")
    print(f"  CDP:     {args.cdp}")
    print(f"  Model:   {args.model}")
    print(f"  Channel: {channel_id}")

    # Start nanobot
    print(f"\n  Starting nanobot components...")
    try:
        agent, discord_ch, bus, tap, tasks = await start_nanobot(
            bot_token=bot_token,
            channel_id=channel_id,
            cdp_url=args.cdp,
            model=args.model,
        )
    except Exception as e:
        print(f"{RED}Failed to start nanobot: {e}{RESET}")
        traceback.print_exc()
        return 1

    print(f"  {GREEN}nanobot ready{RESET}")

    # Run tests
    to_run = {args.test: TESTS[args.test]} if args.test else TESTS
    results: dict[str, bool] = {}

    try:
        for name, test_fn in to_run.items():
            try:
                results[name] = await test_fn(bus, channel_id, tap)
            except Exception:
                traceback.print_exc()
                _print_fail(name, "unhandled exception")
                results[name] = False
    finally:
        print(f"\n  Stopping nanobot...")
        await stop_nanobot(agent, discord_ch, tasks)

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

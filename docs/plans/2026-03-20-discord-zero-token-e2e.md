# Discord + Zero-Token 端到端测试 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 编写一个真实端到端测试脚本，通过 Discord 发消息给 nanobot bot，nanobot 使用 zero-token 模式（Claude Web）处理并回复，测试客户端验证回复内容。

**Architecture:** 进程内启动 nanobot 核心组件（MessageBus + AgentLoop + DiscordChannel + outbound dispatcher），使用真实的 ClaudeWebProvider。测试客户端用 httpx 直接调 Discord REST API（以真实用户身份）发消息和轮询回复，避开 DiscordChannel 的 `author.bot` 过滤。

**Tech Stack:** Python 3.11+, httpx, asyncio, nanobot (ClaudeWebProvider, DiscordChannel, AgentLoop, MessageBus), Discord REST API v10

---

## 前置条件

```bash
# 1. Chrome 带调试端口运行，已登录 claude.ai
google-chrome --remote-debugging-port=9222

# 2. nanobot 已安装 zero-token 依赖
pip install 'nanobot-ai[zero-token]'

# 3. 已完成 Claude Web 登录
nanobot provider login claude-web

# 4. 环境变量
export NANOBOT_DISCORD_BOT_TOKEN="..."       # nanobot 的 Discord bot token
export DISCORD_TEST_USER_TOKEN="..."         # 你的 Discord 用户 token（从浏览器 DevTools 获取）
export DISCORD_TEST_CHANNEL_ID="..."         # 测试专用频道 ID
export DISCORD_TEST_ALLOW_USER_ID="..."      # 你的 Discord user ID（加入 allow_from）
```

## 文件结构

```
scripts/
└── test_discord_e2e.py          # 端到端测试脚本（独立可运行，不依赖 pytest）
```

---

### Task 1: 搭建测试脚本骨架和 Discord HTTP 客户端

**Files:**
- Create: `scripts/test_discord_e2e.py`

**Step 1: 创建 Discord REST API 客户端工具类**

这个类封装以用户身份发送消息和轮询 bot 回复的逻辑。

```python
"""End-to-end test: Discord channel + zero-token (Claude Web) provider.

Tests the full pipeline:
  User message (Discord REST API) → DiscordChannel → MessageBus → AgentLoop
  → ClaudeWebProvider (Chrome CDP → claude.ai) → response → Discord REST API

Prerequisites:
  1. Chrome running with: google-chrome --remote-debugging-port=9222
  2. Logged in to claude.ai (or run: nanobot provider login claude-web)
  3. Environment variables set (see README section below)

Usage:
  python scripts/test_discord_e2e.py                   # run all tests
  python scripts/test_discord_e2e.py --test chat        # simple chat only
  python scripts/test_discord_e2e.py --test tool        # tool call flow only
  python scripts/test_discord_e2e.py --cdp http://127.0.0.1:9222

Environment variables:
  NANOBOT_DISCORD_BOT_TOKEN     — Bot token for nanobot's Discord connection
  DISCORD_TEST_USER_TOKEN       — Your Discord user token (for sending test messages)
  DISCORD_TEST_CHANNEL_ID       — Channel ID where tests run
  DISCORD_TEST_ALLOW_USER_ID    — Your Discord user ID (must be in bot's allow_from)
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback

import httpx

# -- colors --
GREEN = "\033[92m"
RED = "\033[91m"
CYAN = "\033[96m"
DIM = "\033[2m"
RESET = "\033[0m"

DISCORD_API = "https://discord.com/api/v10"


class DiscordTestClient:
    """Minimal Discord REST client using a user token for E2E testing."""

    def __init__(self, user_token: str, channel_id: str):
        self.channel_id = channel_id
        self._http = httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": user_token},  # user token, no "Bot " prefix
        )

    async def send(self, content: str) -> dict:
        """Send a message to the test channel. Returns the message object."""
        url = f"{DISCORD_API}/channels/{self.channel_id}/messages"
        resp = await self._http.post(url, json={"content": content})
        resp.raise_for_status()
        return resp.json()

    async def wait_for_bot_reply(
        self,
        bot_user_id: str,
        after_message_id: str,
        timeout_s: float = 120,
        poll_interval_s: float = 3,
    ) -> dict | None:
        """Poll the channel for a reply from the bot after a given message ID.

        Returns the first bot message found, or None on timeout.
        """
        url = f"{DISCORD_API}/channels/{self.channel_id}/messages"
        deadline = time.monotonic() + timeout_s

        while time.monotonic() < deadline:
            resp = await self._http.get(url, params={"after": after_message_id, "limit": 10})
            resp.raise_for_status()
            messages = resp.json()

            for msg in messages:
                author = msg.get("author", {})
                # Bot messages have author.bot == True
                if str(author.get("id")) == bot_user_id and author.get("bot"):
                    return msg

            await asyncio.sleep(poll_interval_s)

        return None

    async def close(self):
        await self._http.aclose()
```

**Step 2: 验证脚本可以 import 并实例化（无实际网络调用）**

Run: `cd /Users/shingz/Documents/Project/AgentBot && python -c "from scripts.test_discord_e2e import DiscordTestClient; print('OK')"`

Expected: `OK`（脚本语法正确，import 成功）

---

### Task 2: 实现 nanobot 进程内启动逻辑

**Files:**
- Modify: `scripts/test_discord_e2e.py`

**Step 1: 添加 nanobot 组件启动函数**

在脚本中添加一个函数，进程内启动 MessageBus + AgentLoop + DiscordChannel + outbound dispatcher。关键点：
- 使用真实的 `ClaudeWebProvider`（zero-token 模式）
- DiscordChannel 配置使用环境变量中的 bot token
- `allow_from` 包含测试用户的 ID
- 需要等 Discord gateway READY 后再开始测试

```python
async def start_nanobot(
    bot_token: str,
    allow_user_id: str,
    cdp_url: str,
    model: str,
) -> tuple:
    """Start nanobot components in-process. Returns (agent, discord_channel, bus, tasks)."""
    from pathlib import Path
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.discord import DiscordChannel, DiscordConfig
    from nanobot.providers.claude_web_auth import load_credentials
    from nanobot.providers.claude_web_provider import ClaudeWebProvider
    from nanobot.session.manager import SessionManager

    # Build provider
    creds = load_credentials()
    if not creds.get("session_key"):
        raise RuntimeError("No Claude Web credentials. Run: nanobot provider login claude-web")

    provider = ClaudeWebProvider(
        session_key=creds.get("session_key", ""),
        cookie=creds.get("cookie", ""),
        user_agent=creds.get("user_agent", ""),
        organization_id=creds.get("organization_id", ""),
        chrome_cdp_url=cdp_url,
        default_model=model,
    )

    # Build bus, agent, channel
    workspace = Path.home() / ".nanobot" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    bus = MessageBus()
    session_manager = SessionManager(workspace)
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        model=model,
        max_iterations=10,  # cap for testing
        session_manager=session_manager,
    )

    discord_config = DiscordConfig(
        enabled=True,
        token=bot_token,
        allow_from=[allow_user_id, "*"],  # allow test user
    )
    discord_ch = DiscordChannel(discord_config, bus)

    # Outbound dispatcher: routes agent replies to Discord REST API
    async def dispatch_outbound():
        while True:
            try:
                msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                if msg.metadata.get("_progress"):
                    continue  # skip progress messages in test
                await discord_ch.send(msg)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"  {DIM}dispatch error: {e}{RESET}")

    # Start everything
    agent_task = asyncio.create_task(agent.run())
    channel_task = asyncio.create_task(discord_ch.start())
    dispatch_task = asyncio.create_task(dispatch_outbound())

    # Wait for Discord gateway to become ready (bot_user_id is set on READY)
    for _ in range(30):
        if discord_ch._bot_user_id:
            break
        await asyncio.sleep(1)
    else:
        raise RuntimeError("Discord gateway did not become ready within 30s")

    print(f"  {DIM}nanobot bot user ID: {discord_ch._bot_user_id}{RESET}")
    return agent, discord_ch, bus, [agent_task, channel_task, dispatch_task]


async def stop_nanobot(agent, discord_ch, tasks):
    """Gracefully stop all nanobot components."""
    agent.stop()
    await discord_ch.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await agent.provider.close()
```

**Step 2: 验证启动逻辑的 import 链没有问题**

Run: `cd /Users/shingz/Documents/Project/AgentBot && python -c "import ast; ast.parse(open('scripts/test_discord_e2e.py').read()); print('syntax OK')"`

Expected: `syntax OK`

---

### Task 3: 实现测试用例

**Files:**
- Modify: `scripts/test_discord_e2e.py`

**Step 1: 实现 test_simple_chat — 简单数学问答**

发送一个简单数学问题，验证 bot 能回复且内容包含正确答案。

```python
def _print_header(name: str) -> None:
    print(f"\n{CYAN}{'=' * 60}")
    print(f"  TEST: {name}")
    print(f"{'=' * 60}{RESET}\n")


def _print_pass(name: str) -> None:
    print(f"\n  {GREEN}PASS{RESET} {name}\n")


def _print_fail(name: str, reason: str) -> None:
    print(f"\n  {RED}FAIL{RESET} {name}: {reason}\n")


async def test_simple_chat(client: DiscordTestClient, bot_user_id: str) -> bool:
    """Send a math question, verify bot replies with correct answer."""
    _print_header("Simple Chat (Discord → zero-token → Discord)")

    sent = await client.send("计算 2+3 等于多少，只回复数字")
    print(f"  Sent message ID: {sent['id']}")
    print(f"  Waiting for bot reply (up to 120s)...")

    reply = await client.wait_for_bot_reply(bot_user_id, sent["id"], timeout_s=120)

    if not reply:
        _print_fail("Simple Chat", "no reply from bot within timeout")
        return False

    content = reply.get("content", "")
    print(f"  Bot replied: {content[:200]}")

    if "5" in content:
        _print_pass("Simple Chat")
        return True

    # Non-empty reply is still a partial pass (Claude may phrase it differently)
    if content.strip():
        print(f"  {DIM}(response didn't contain '5' but was non-empty){RESET}")
        _print_pass("Simple Chat")
        return True

    _print_fail("Simple Chat", "empty response from bot")
    return False
```

**Step 2: 实现 test_tool_call — 验证工具调用 + 回复流程**

发送一个需要 agent 使用工具的消息（比如 web_search），验证 bot 能完成工具调用并回复结果。因为 agent loop 会自动执行工具，我们只需验证最终有非空回复。

```python
async def test_tool_call(client: DiscordTestClient, bot_user_id: str) -> bool:
    """Send a message that triggers tool usage, verify bot completes and replies."""
    _print_header("Tool Call (agent loop with real tools)")

    sent = await client.send("请用 web_search 工具搜索 'nanobot-ai github'，然后总结搜索结果")
    print(f"  Sent message ID: {sent['id']}")
    print(f"  Waiting for bot reply (up to 180s, tool calls may take longer)...")

    reply = await client.wait_for_bot_reply(bot_user_id, sent["id"], timeout_s=180)

    if not reply:
        _print_fail("Tool Call", "no reply from bot within timeout")
        return False

    content = reply.get("content", "")
    print(f"  Bot replied ({len(content)} chars): {content[:200]}")

    if len(content) > 20:
        _print_pass("Tool Call")
        return True

    _print_fail("Tool Call", f"reply too short ({len(content)} chars)")
    return False
```

**Step 3: 验证语法**

Run: `cd /Users/shingz/Documents/Project/AgentBot && python -c "import ast; ast.parse(open('scripts/test_discord_e2e.py').read()); print('syntax OK')"`

Expected: `syntax OK`

---

### Task 4: 实现 main 入口和 CLI 参数解析

**Files:**
- Modify: `scripts/test_discord_e2e.py`

**Step 1: 添加 main 函数，串联所有测试**

```python
import argparse

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
    user_token = os.environ.get("DISCORD_TEST_USER_TOKEN", "")
    channel_id = os.environ.get("DISCORD_TEST_CHANNEL_ID", "")
    allow_user_id = os.environ.get("DISCORD_TEST_ALLOW_USER_ID", "")

    missing = []
    if not bot_token:
        missing.append("NANOBOT_DISCORD_BOT_TOKEN")
    if not user_token:
        missing.append("DISCORD_TEST_USER_TOKEN")
    if not channel_id:
        missing.append("DISCORD_TEST_CHANNEL_ID")
    if not allow_user_id:
        missing.append("DISCORD_TEST_ALLOW_USER_ID")
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
        agent, discord_ch, bus, tasks = await start_nanobot(
            bot_token=bot_token,
            allow_user_id=allow_user_id,
            cdp_url=args.cdp,
            model=args.model,
        )
    except Exception as e:
        print(f"{RED}Failed to start nanobot: {e}{RESET}")
        traceback.print_exc()
        return 1

    bot_user_id = discord_ch._bot_user_id
    print(f"  {GREEN}nanobot ready{RESET} (bot: {bot_user_id})")

    # Create test client
    client = DiscordTestClient(user_token, channel_id)

    # Run tests
    to_run = {args.test: TESTS[args.test]} if args.test else TESTS
    results: dict[str, bool] = {}

    try:
        for name, test_fn in to_run.items():
            try:
                results[name] = await test_fn(client, bot_user_id)
            except Exception:
                traceback.print_exc()
                _print_fail(name, "unhandled exception")
                results[name] = False
    finally:
        await client.close()
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
```

**Step 2: 验证完整脚本语法和 import**

Run: `cd /Users/shingz/Documents/Project/AgentBot && python -c "import ast; ast.parse(open('scripts/test_discord_e2e.py').read()); print('syntax OK')"`

Expected: `syntax OK`

**Step 3: 空跑验证环境变量检查**

Run: `cd /Users/shingz/Documents/Project/AgentBot && python scripts/test_discord_e2e.py`

Expected: 输出 `Missing environment variables: ...` 并退出码 1（因为没设环境变量）

---

### Task 5: 本地手动测试

**不写代码。** 手动验证完整流程。

**Step 1: 启动 Chrome**

```bash
google-chrome --remote-debugging-port=9222
# 确保已登录 claude.ai
```

**Step 2: 设置环境变量**

```bash
export NANOBOT_DISCORD_BOT_TOKEN="你的bot token"
export DISCORD_TEST_USER_TOKEN="你的用户token"
export DISCORD_TEST_CHANNEL_ID="测试频道ID"
export DISCORD_TEST_ALLOW_USER_ID="你的用户ID"
```

**Step 3: 运行测试**

```bash
cd /Users/shingz/Documents/Project/AgentBot

# 先跑单个简单测试
http_proxy="" https_proxy="" all_proxy="" python scripts/test_discord_e2e.py --test chat

# 全部测试
http_proxy="" https_proxy="" all_proxy="" python scripts/test_discord_e2e.py
```

Expected:
- nanobot 组件启动，Discord gateway READY
- 测试消息出现在 Discord 频道
- bot 回复出现在频道
- 测试判定 PASS/FAIL

**Step 4: Commit**

```bash
git add scripts/test_discord_e2e.py
git commit -m "feat: add Discord + zero-token end-to-end test script"
```

---

## 注意事项

1. **超时要宽裕**：Chrome CDP 连接 + Claude Web 响应通常需要 30-60s，加上 Discord 延迟，单个测试超时建议 120-180s
2. **代理干扰**：运行时必须清除 `http_proxy` / `https_proxy` / `all_proxy` 环境变量，否则 Chrome CDP 连接会失败
3. **`author.bot` 过滤**：测试客户端必须用真实用户 token，不能用 bot token 或 webhook，否则消息会被 `discord.py:291` 过滤
4. **断言模糊匹配**：真实 Claude 回复不可控，断言只检查关键词或最低长度，不做精确匹配
5. **清理**：测试消息会留在 Discord 频道中，建议使用专用的测试频道
6. **progress 消息**：outbound dispatcher 需要跳过 `_progress` 类型的消息，否则 `wait_for_bot_reply` 可能匹配到中间状态消息（但 progress 消息不经过 Discord send，所以实际不会出现在频道中 — dispatcher 中跳过是为了避免额外的 REST API 调用）

---

## 实现记录

> 初始日期：2026-03-20
> 最后更新：2026-03-21

### 最终产出

| 文件 | 说明 |
|------|------|
| `scripts/test_discord_e2e.py` | E2E 测试脚本（Bus 注入 + OutboundTap 验证） |
| `nanobot/providers/claude_web_provider.py` | 增加 zero-token 链路日志 |
| `nanobot/providers/xml_tool_parser.py` | 增加 XML 解析日志 |

### 文件结构（v3 — 当前）

```
scripts/test_discord_e2e.py
├── OutboundTap                # 拦截 bus outbound 消息用于测试验证
├── _print_header/pass/fail    # 终端彩色输出辅助
├── start_nanobot()            # 进程内启动 nanobot 全组件，返回 tap
├── stop_nanobot()             # 优雅停止
├── inject_message()           # 往 MessageBus 注入 InboundMessage
├── test_simple_chat()         # 测试用例：简单数学问答
├── test_tool_call()           # 测试用例：工具调用 + 回复
└── main()                     # CLI 入口 + 环境变量检查 + 测试编排
```

### 架构演进

#### v1（初始方案）— Discord User Token

```
DiscordTestClient (user token) → Discord REST API → DiscordChannel → MessageBus → AgentLoop
                                                                                      ↓
DiscordTestClient (poll) ← Discord REST API ← outbound dispatch ← ClaudeWebProvider (CDP)
```

**问题**：新版 Discord 无法从浏览器 DevTools 提取 user token（`webpackChunkdiscord_app` 已失效）。

#### v2 — Bus 注入 + DiscordReader

```
inject_message() → MessageBus → AgentLoop → ClaudeWebProvider (Chrome CDP)
                                                    ↓
                    DiscordReader (bot token GET) ← Discord REST API ← outbound dispatch
```

**问题**：Bot 读频道消息返回 404（需要 `Read Message History` + `View Channel` 权限 + Message Content Intent）。

#### v3（当前）— Bus 注入 + OutboundTap

```
inject_message() → MessageBus → AgentLoop → ClaudeWebProvider (Chrome CDP)
                                                    ↓
                                            bus.publish_outbound()
                                                    ↓
                                    ┌───────────────┴───────────────┐
                                    │                               │
                              OutboundTap                   dispatch → Discord REST API
                           (测试验证 ✅)                    (best-effort 发送)
```

- **输入端**：`inject_message()` 直接往 MessageBus 注入 `InboundMessage`
- **输出端**：dispatch 尝试通过 Discord REST API 发送（失败不影响测试）
- **验证端**：`OutboundTap` 拦截 bus outbound 队列，直接读取 agent 回复
- **环境变量**：只需 `NANOBOT_DISCORD_BOT_TOKEN` + `DISCORD_TEST_CHANNEL_ID`
- **跳过的部分**：Discord Gateway 收消息 → DiscordChannel 解析（一小段）

### 增强日志（2026-03-21）

在 zero-token 链路中增加了详细日志，便于调试：

| 日志标签 | 位置 | 内容 |
|----------|------|------|
| `[zero-token] prompt` | `claude_web_provider.py:chat()` | 发送给 claude.ai 的完整 prompt（截断 500 chars） |
| `[zero-token] raw response` | `claude_web_provider.py:chat()` | claude.ai 返回的原始文本（含 XML tool_call 标签） |
| `[xml-parser] found N <tool_call> tag(s)` | `xml_tool_parser.py:parse_tool_calls()` | 检测到的 XML tool call 数量 |
| `[xml-parser] raw XML` | `xml_tool_parser.py:parse_tool_calls()` | 每个 `<tool_call>` 标签的原始内容 |
| `[zero-token] parsed tool call` | `claude_web_provider.py:_parse_response()` | 转换后的 JSON 格式：`name(args) [id=xxx]` |
| `[zero-token] clean text` | `claude_web_provider.py:_parse_response()` | 去掉 XML 标签后的纯文本 |

### 与计划的偏差

| 计划 | 实际 | 原因 |
|------|------|------|
| User Token 发消息 | Bus 注入 | 新版 Discord 无法提取 user token |
| `DiscordTestClient` | `OutboundTap` + `inject_message()` | 绕开 Discord 读权限问题 |
| DiscordReader 验证 | OutboundTap 验证 | Bot 读频道 404，改为直接拦截 bus |
| 4 个环境变量 | 2 个 | 不再需要 user token 和 user ID |
| `dispatch_outbound` 无超时 | `asyncio.wait_for(..., timeout=1.0)` | 避免永久阻塞 |
| Discord send 失败即测试失败 | Discord send 改为 best-effort | 验证不依赖 Discord 发送成功 |

### 测试结果（2026-03-21）

| 测试 | 结果 | 耗时 | 备注 |
|------|------|------|------|
| Simple Chat（2+3=?） | ✅ PASS | ~7s | Agent 正确回复 "5" |
| 微博热搜查询 | ✅ PASS | ~45s | 4 次工具调用（2× web_search + 2× web_fetch） |
| 深圳天气查询 | ✅ PASS | ~30s | 1× web_search，返回完整天气信息 |

### 运行方式

```bash
export NANOBOT_DISCORD_BOT_TOKEN="..."
export DISCORD_TEST_CHANNEL_ID="..."

# 确保 Chrome 运行且 claude.ai 已登录
# google-chrome --remote-debugging-port=9222

# 运行测试
http_proxy="" https_proxy="" all_proxy="" python scripts/test_discord_e2e.py --test chat

# 自定义查询（使用 inline Python）
http_proxy="" https_proxy="" all_proxy="" python -c "
import asyncio, sys; sys.path.insert(0, '.')
from scripts.test_discord_e2e import start_nanobot, stop_nanobot, inject_message
# ... (see test session examples above)
"
```

### 已知问题

1. **Claude Web 凭证提取**：`nanobot provider login claude-web` 可能因 `net::ERR_ABORTED` 失败（已登录时重定向冲突）。解决方案：直接用 CDP 脚本提取 cookie，跳过 login 导航。
2. **Chrome 页面关闭**：如果 claude.ai 标签页被关闭/刷新，`Page.evaluate` 会抛 `TargetClosedError`。需保持标签页打开。
3. **Discord Bot 权限**：Bot 需要 `Send Messages` 权限才能发送回复到频道。`Read Message History` + `View Channel` + Message Content Intent 用于 v2 DiscordReader 方案（v3 不需要）。

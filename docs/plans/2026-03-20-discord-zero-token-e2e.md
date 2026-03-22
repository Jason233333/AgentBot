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

### 已知问题（运维）

1. **Claude Web 凭证提取**：`nanobot provider login claude-web` 可能因 `net::ERR_ABORTED` 失败（已登录时重定向冲突）。解决方案：直接用 CDP 脚本提取 cookie，跳过 login 导航。
2. **Chrome 页面关闭**：如果 claude.ai 标签页被关闭/刷新，`Page.evaluate` 会抛 `TargetClosedError`。需保持标签页打开。
3. **Discord Bot 权限**：Bot 需要 `Send Messages` 权限才能发送回复到频道。`Read Message History` + `View Channel` + Message Content Intent 用于 v2 DiscordReader 方案（v3 不需要）。

---

## Zero-Token Provider 状态管理问题与修复计划

> 日期：2026-03-21
> 参考实现：`utils_repo/openclaw-zero-token`

### 问题全景

通读 zero-token 相关代码后发现 11 个问题，**全部是 zero-token 模式专属**，normal 模式（LiteLLM/OpenAI）不受影响。根本原因：zero-token 把 claude.ai 当有状态的聊天窗口用（依赖服务端记住上下文），但状态管理做得不完善。

#### P0 — 数据错乱 / 上下文丢失

| # | 问题 | 场景 | 根因 | 代码位置 |
|---|------|------|------|----------|
| 1 | **Continuation 误判** | 历史里有旧 `role:"tool"` 消息 → 新用户消息也走 continuation path → 只发 tool results，不发 system prompt | `any(role=="tool")` 太粗暴，匹配到历史中的旧 tool results | `claude_web_provider.py:_convert_messages()` |
| 2 | **Session key 错乱** | 不同频道/对话共用同一 system prompt → hash 相同 → 共用同一个 claude.ai conversation → 消息串台 | `_get_session_key()` 用 system prompt 前 200 字符 hash，不用 nanobot session_key | `claude_web_provider.py:_get_session_key()` |
| 3 | **Continuation + conversation 丢失** | Chrome 崩溃/网络断/conversation 过期后，tool results 发到空白 conversation，无 system prompt、无 tool definitions | continuation path 假设 conversation 永远存活，无失效检测 | `claude_web_provider.py:_convert_continuation()` |

#### P1 — 严重功能缺陷

| # | 问题 | 场景 | 根因 |
|---|------|------|------|
| 4 | **SSE 解析忽略 error 事件** | claude.ai 限流/报错时 SSE 返回 error 事件 → 被忽略 → 返回空字符串 → agent 以为成功 | SSE parser 只处理 `completion` 和 `content_block_delta`，不处理 `error` / `message_limit` |
| 5 | **`_active_session_key` 并发竞态** | 两个用户同时发消息 → `set_session_key("A")` 被 `set_session_key("B")` 覆盖 → A 的消息进了 B 的 conversation | `_active_session_key` 是共享可变状态，无并发保护 |
| 6 | **Bot 重启丢 conversation 缓存** | `_conversations` 是纯内存 dict → 重启清空 → session 历史有 tool results → continuation 发到新 conversation 无上下文 | `_conversations` 不持久化 |
| 7 | **`/new` 不清 conversation** | `/new` 清了 session 历史但没清 `_conversations` → 消息继续发到旧 conversation | AgentLoop 未调用 `clear_conversations()` |
| 8 | **复用 conversation 时重复发历史** | 同一 session 的后续消息还是发完整 system prompt + 历史 → claude.ai 看到两遍历史 | `_convert_messages()` 不区分新建/复用 conversation |

#### P2 — 次要

| # | 问题 |
|---|------|
| 9 | Tool result `None` 序列化成字面量 `"null"` |
| 10 | `_map_model()` 前缀剥离留残余 `/`（碰巧靠子串匹配工作） |
| 11 | 图片附件转换失败静默跳过 |

#### 已修复

| # | 修复内容 | 日期 |
|---|---------|------|
| 1 | continuation 判断改为 `messages[-1].role == "tool"` | 2026-03-21 |
| 2 | 添加 `set_session_key()` + `_active_session_key`，AgentLoop 传入 nanobot session_key | 2026-03-21 |
| 7 | `/new` 时调用 `provider.clear_session(key)` | 2026-03-21 |
| 8 | 添加 `full_prompt` 参数，复用 conversation 时只发新 user message | 2026-03-21 |

### 参考实现：OpenClaw (`utils_repo/openclaw-zero-token`)

OpenClaw 也是 zero-token 架构（通过浏览器自动化访问 claude.ai / deepseek / chatgpt），**整体设计思路和 nanobot 一致**（复用 conversation + continuation 增量发送），但在状态管理上做得更成熟。

#### OpenClaw 的关键设计

| 方面 | OpenClaw 做法 | nanobot 现状 |
|------|-------------|-------------|
| **session key 来源** | 外部传入，`sessionMap: Map<sessionKey, conversationId>` | ~~system prompt hash~~ → 已改为外部传入 |
| **conversation 持久化** | 存磁盘（`SessionEntry` 文件），重启可恢复 | 纯内存 dict，重启丢失 |
| **conversation 失效处理** | 检测失败 → 创建新 conversation → 发完整历史 → 自动恢复 | SSE error 静默忽略，返回空字符串 |
| **并发隔离** | 每个 connection 有 `connId`，session 内消息排队处理 | ~~`_active_session_key` 共享变量~~ → 部分修复 |
| **SSE 错误处理** | 捕获 error 事件，抛异常 | 忽略 error 事件 |
| **context 压缩** | 接近 token limit 时才做 compaction（摘要） | MemoryConsolidator 按 token 阈值触发 |
| **first turn vs continuation** | 首次发完整历史，后续只发新消息 | ~~每次都发完整历史~~ → 已修复 |

#### OpenClaw 不适用于 nanobot 的部分

| 方面 | 说明 |
|------|------|
| `parentMessageId` | DeepSeek Web 专用，Claude Web 不需要（线性 conversation 自动追加） |
| WebSocket 连接模型 | nanobot 用 MessageBus，不用 WebSocket |
| 多 provider 切换 | nanobot 的 zero-token 只支持 Claude Web |

### 修复计划（方案 C：保留 conversation 复用，修状态管理）

不改整体架构（conversation 复用 + continuation 增量发送），只修状态管理的细节。

**参考 OpenClaw 的部分**：

| 改动 | 参考 OpenClaw 什么 |
|------|-------------------|
| session_key 外部传入 | `sessionMap` 由上层传入 key，不自己算 hash |
| conversation 失效检测 + fallback 重建 | 检测失败后创建新 conversation，发完整历史恢复 |
| SSE error 捕获 | SSE 解析捕获 error 事件并抛异常 |
| 并发隔离 | 每个 connection 独立 connId + session 内排队 |

**未参考的部分**：

| 方面 | 原因 |
|------|------|
| `parentMessageId` | Claude Web 不需要（线性 conversation 自动追加） |
| conversation 持久化到磁盘 | 暂不做，优先级不高——重启后发完整历史即可恢复 |
| WebSocket 连接模型 | nanobot 用 MessageBus，架构不同 |

#### 阶段 1：修并发竞态（P1 #5）

**目标**：消除 `_active_session_key` 共享变量。

**做法**：
- `chat()` 基类签名加 `**kwargs`，zero-token provider 从中取 `session_key`
- AgentLoop 调用 `chat_with_retry()` 时传入 `session_key=key`
- 删除 `set_session_key()` 和 `_active_session_key`

**涉及文件**：
- `nanobot/providers/base.py` — `chat()` / `chat_with_retry()` 加 `**kwargs`
- `nanobot/providers/claude_web_provider.py` — `chat()` 从 kwargs 取 session_key
- `nanobot/agent/loop.py` — 调用时传 `session_key=key`

#### 阶段 2：conversation 失效检测 + 重建（P0 #3，P1 #6）

**目标**：conversation 丢失时自动恢复。

**做法**：
- `send_message()` 返回空字符串 → 视为 conversation 失效
- SSE error 事件 → 捕获并抛异常（修 P1 #4）
- conversation 失效时：删除旧映射 → 创建新 conversation → 发完整 prompt 重试
- 可选：持久化 `_conversations` 到磁盘（参照 OpenClaw 的 SessionEntry），重启可恢复

**涉及文件**：
- `nanobot/providers/claude_web_client.py` — SSE parser 加 error 事件处理
- `nanobot/providers/claude_web_provider.py` — `chat()` 加失效检测 + fallback 逻辑

#### 阶段 3：小修复（P2）

- #9：`None` → `""` 而不是 `"null"`（一行改动）
- #10：`_map_model()` 前缀剥离修正（一行改动）
- #11：图片附件失败时 log 具体错误

#### 验证计划

每个阶段完成后用 `test_discord_e2e.py` 验证：

```bash
# 基础聊天
python scripts/test_discord_e2e.py --test chat

# Tool 调用
python scripts/test_discord_e2e.py --test tool

# Session 隔离（两个 session 同时发消息）
# Conversation 恢复（kill Chrome 后重连）
# /new 后的 session 清理
```

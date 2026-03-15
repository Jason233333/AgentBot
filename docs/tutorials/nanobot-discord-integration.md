# nanobot Discord 集成深度解析

> 项目：nanobot (HKUDS/nanobot)
> 代码版本：0.1.4.post4: 65cbd7eb78672e226a8108c81da3ed8ce50ab192
> 涉及文件：
>
> - `nanobot/channels/discord.py`（Discord Gateway 实现，378 行）
> - `nanobot/channels/base.py`（BaseChannel 接口，135 行）
> - `nanobot/channels/manager.py`（频道管理和路由，156 行）
> - `nanobot/channels/registry.py`（动态频道发现，36 行）
> - `nanobot/bus/events.py`（InboundMessage / OutboundMessage 数据结构）
> - `nanobot/bus/queue.py`（MessageBus 双队列）
> - `nanobot/agent/loop.py`（AgentLoop 消息处理循环）
> - `nanobot/agent/tools/message.py`（message 工具，跨频道发送）
> - `nanobot/config/schema.py:63-71`（DiscordConfig 配置定义）

---

## 1. 设计概览

nanobot 的 Discord 集成是**原生 WebSocket 实现**，没有使用 discord.py 等第三方库，直接对接 Discord Gateway API v10。整体架构遵循 nanobot 的 Channel → MessageBus → AgentLoop 消息驱动模型。

### 1.1 配置项

```python
# schema.py:63-71
class DiscordConfig:
    enabled: bool
    token: str                    # Discord bot token
    allow_from: list[str]         # 允许的用户 ID 列表，"*" 表示全部允许
    gateway_url: str              # 默认 wss://gateway.discord.gg/?v=10&encoding=json
    intents: int                  # 默认 37377 = GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT
    group_policy: "mention" | "open"  # 群组响应策略
```

- `group_policy="mention"`：只响应被 @bot 的消息
- `group_policy="open"`：响应群组内所有消息

---

## 2. 完整生命周期

### 2.1 连接阶段

```
nanobot gateway 启动
    ↓
ChannelManager._init_channels()
    → registry.discover_channel_names() 扫描 channels/ 目录
    → 发现 discord 模块，检查 config.channels.discord.enabled
    → 实例化 DiscordChannel(config, bus)
    ↓
ChannelManager.start() → DiscordChannel.start()
    ↓
WebSocket 连接 gateway.discord.gg
    → 收到 op=10 HELLO（含 heartbeat_interval）
    → 启动心跳协程（每 interval 发送 op=1）
    → 发送 op=2 IDENTIFY（token + intents）
    → 收到 READY 事件（获取 bot_user_id）
    → 进入消息监听循环
```

断线时自动重连（5 秒延迟），支持 Discord 的 op=7 RECONNECT 和 op=9 INVALID_SESSION 指令。

### 2.2 入站流程（用户消息 → Agent）

```
Discord 用户在频道中发消息
    ↓
Gateway 推送 op=0, event_type="MESSAGE_CREATE"
    ↓
过滤检查：
    ① 忽略 bot 发送的消息（author.bot == true）
    ② 权限检查：sender_id 在 allow_from 列表中？
    ③ 群组策略：group_policy="mention" 时，检查是否 @了 bot
    ↓（全部通过）
解析消息内容：
    - 提取 sender_id, channel_id, guild_id, message_id, reply_to
    - 下载附件（≤20MB）→ 保存到 ~/.nanobot/data/media/discord/
    ↓
构造 InboundMessage：
    InboundMessage(
        channel="discord",
        sender_id=sender_id,
        chat_id=channel_id,        # Discord 频道 ID
        content=文本内容,
        media=[本地文件路径列表],
        metadata={
            "message_id": message_id,
            "guild_id": guild_id,
            "reply_to": reply_to,    # 引用消息 ID
        }
    )
    ↓
发布到 MessageBus.inbound 队列
    ↓
AgentLoop._dispatch() 获取 _processing_lock
    ↓
ContextBuilder 组装 [system_prompt, *session_history, user_message]
    ↓
LLM 调用 → 可能多轮 tool 执行 → 最终回复
```

### 2.3 出站流程（Agent → Discord）

```
Agent 产出 OutboundMessage(channel="discord", chat_id=channel_id)
    ↓
ChannelManager._dispatch_outbound() 从 bus.outbound 消费
    → 根据 msg.channel 路由到 DiscordChannel.send()
    → 过滤 progress/tool_hints 类消息（根据配置）
    ↓
DiscordChannel.send():
    ① 先发媒体文件（multipart/form-data 上传到 Discord API）
    ② 再发文本（超过 2000 字符自动分割）
    ↓
速率限制处理：
    遇到 HTTP 429 → 读 retry_after 头 → sleep → 重试（最多 3 次）
```

### 2.4 辅助机制

**Typing 指示器**：处理消息期间，每 8 秒向 Discord POST 一次 typing endpoint，显示"正在输入…"效果。每个频道独立一个 typing 协程。

**心跳维持**：独立协程按 Discord 要求的 interval 发送 op=1，保持 WebSocket 连接活跃。序列号 `_seq` 随每条消息递增。

**消息分割**（`helpers.py:split_message()`）：

- 优先在换行符处断开
- 其次在空格处断开
- 最后硬截断
- 每段 ≤2000 字符（Discord 单条消息限制）

---

## 3. 频道注册与发现机制

nanobot 的频道系统是**零硬编码的插件式架构**：

```python
# registry.py
def discover_channel_names() -> list[str]:
    """扫描 nanobot/channels/ 目录，排除 base/manager/registry，返回模块名列表"""

def load_channel_class(name: str) -> type[BaseChannel]:
    """动态导入模块，查找 BaseChannel 子类"""
```

```python
# manager.py:_init_channels()
for modname in discover_channel_names():      # ["discord", "slack", "telegram", ...]
    section = getattr(config.channels, modname)
    if not section or not section.enabled:
        continue
    cls = load_channel_class(modname)          # DiscordChannel
    channel = cls(section, bus)
    channel.transcription_api_key = groq_key   # 音频转录 API
    self.channels[modname] = channel
```

新增一个平台只需：在 `channels/` 下新建模块，实现 `BaseChannel` 子类，在配置中添加对应 section。无需修改任何已有代码。

---

## 4. FAQ

### Q1: 一个 agent 在多个 channel，消息会不会串？

**不会串。** 隔离机制是 `session_key`：

```python
# events.py — InboundMessage.session_key
@property
def session_key(self) -> str:
    return self.session_key_override or f"{self.channel}:{self.chat_id}"
# 例如："discord:123456789" 和 "discord:987654321" 是两个独立会话
```

每个 Discord 频道有独立的 session_key → 独立的 Session 实例 → 独立的消息历史和 JSONL 文件 → 独立的 consolidation 状态。频道 A 的对话历史不会出现在频道 B 的 LLM prompt 里。

**唯一的共享点是 MEMORY.md**。所有频道共享同一份长期记忆。如果 agent 在频道 A 学到某个事实并写入 MEMORY.md，频道 B 的 system prompt 中也会包含它。这是 by design——长期记忆是 agent 级的，不是 session 级的。

**与其他平台的对比**：

| 平台     | session_key 格式                 | 线程/话题隔离 |
| -------- | -------------------------------- | ------------- |
| Discord  | `discord:{channel_id}`           | ❌ 不支持     |
| Slack    | `slack:{channel_id}:{thread_ts}` | ✅ 线程级隔离 |
| Telegram | `telegram:{chat_id}` 或含 topic  | ✅ 话题级隔离 |

### Q2: 能扛住多大的并发？

**瓶颈在 `_processing_lock`——这是一把全局 `asyncio.Lock`。**

```python
# loop.py
self._processing_lock = asyncio.Lock()
```

`_dispatch()` 处理每条消息时都会获取这把锁，意味着：

- **同一时刻只有一条消息在被 LLM 处理**——不区分频道，全局串行
- 后到的消息在 asyncio.Queue 中排队等待
- 入站队列本身无大小限制（asyncio.Queue 默认无界）

**实际吞吐量 ≈ 1 / 单次处理耗时。** 如果一次 LLM 调用（含多轮 tool 执行）耗时 10 秒，吞吐量约 6 条/分钟。

**并行点**：

- subagent 是独立的 `asyncio.Task`，不受主循环锁约束，可以后台并行执行
- 但 subagent 结果回注（通过 `channel="system"` InboundMessage）后，仍需排队进入主循环

**外部限制**：

- Discord API 全局速率限制：约 50 msg/s/token
- 单频道速率限制：5 msg/5s（发送侧已有 429 重试处理）

**结论：当前设计是单并发处理，适合单 bot 轻量使用场景。如需高并发，需要改造为会话级锁（而非全局锁）或多实例部署。**

### Q3: 能接收、回复图片、音频、文件吗？

**接收能力：**

| 类型     | 支持 | 处理方式                                                                                 |
| -------- | ---- | ---------------------------------------------------------------------------------------- |
| 图片     | ✅   | 下载到本地 → `context.py` 中 base64 编码 → 作为 `image_url` content block 发给多模态 LLM |
| 音频     | ✅   | 下载到本地 → 若配置了 `transcription_api_key`（Groq），自动转录为文本附在消息中          |
| 其他文件 | ✅   | 下载到本地（≤20MB）→ 文件路径传给 agent，agent 可用 `read_file` 工具读取内容             |
| 超大文件 | ❌   | >20MB 跳过下载，替换为 `[attachment: filename - too large]` 文本提示                     |

```python
# discord.py — 附件处理
for attachment in payload.get("attachments") or []:
    if size > MAX_ATTACHMENT_BYTES:  # 20MB
        content_parts.append(f"[attachment: {filename} - too large]")
        continue
    # 下载到 ~/.nanobot/data/media/discord/{attachment_id}_{filename}
    file_path = media_dir / f"{attachment_id}_{filename}"
    resp = await self._http.get(url)
    file_path.write_bytes(resp.content)
    media_paths.append(str(file_path))
```

**回复能力：**

| 类型      | 支持 | 处理方式                                                                      |
| --------- | ---- | ----------------------------------------------------------------------------- |
| 纯文本    | ✅   | 自动分割为 ≤2000 字符的分段逐条发送                                           |
| 文件/图片 | ✅   | agent 用 `message` 工具指定 `media` 参数 → multipart/form-data 上传到 Discord |
| 音频      | ✅   | 同文件，作为附件发送                                                          |
| 超大文件  | ❌   | >20MB 时 log warning 并跳过                                                   |

```python
# discord.py — 出站文件发送
async def _send_file(self, url, headers, file_path, reply_to=None) -> bool:
    if path.stat().st_size > MAX_ATTACHMENT_BYTES:
        logger.warning("File too large, skipping")
        return False
    files = {"files[0]": (path.name, f, "application/octet-stream")}
    response = await self._http.post(url, headers=headers, files=files, data=data)
```

### Q4: 在一个 Discord channel 里，一个 agent 能给另一个 agent 发消息吗？

**不能。** 有三层阻隔：

**第一层：架构隔离。** 一个 nanobot 实例 = 一个 bot token = 一个 agent。多个 agent 需要运行多个独立的 nanobot 进程。

**第二层：Discord 规则。** Bot 发送的消息带有 `author.bot = true` 标记。

**第三层：代码显式过滤。**

```python
# discord.py — 消息处理时
if author.get("bot"):
    return  # 忽略所有 bot 消息，防止循环对话
```

即使两个 bot 在同一个 Discord 频道，Agent A 发的消息会被 Agent B 的 nanobot 直接丢弃。**这是防止 bot 无限循环对话的安全设计。**

**Subagent 不是"另一个 agent"**：它是同一个 agent 内部的后台 `asyncio.Task`，通过内存中的 MessageBus 通信（`channel="system"`），完全不经过 Discord。

**如果确实需要 agent 间通信的替代方案：**

- 共享文件系统：两个 agent 读写同一目录下的文件
- 自定义内部 Channel：实现一个基于 IPC/消息队列的 BaseChannel 子类
- 外部协调：通过数据库或 Redis 等中间件

---

## 5. 完整消息流转图

```
┌─────────────────────────────────────────────────────────────────┐
│                        Discord Server                           │
│                                                                 │
│  User 发消息 ──→ Gateway WebSocket ──→ MESSAGE_CREATE 事件       │
│  User 收消息 ←── REST API POST     ←── 分段文本 / 文件上传       │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                    WebSocket 连接
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                    DiscordChannel                                │
│                                                                 │
│  _gateway_loop():                                               │
│    ├─ op=10 HELLO → 启动心跳 + IDENTIFY                         │
│    ├─ op=0 MESSAGE_CREATE:                                      │
│    │   ├─ 过滤 bot 消息                                          │
│    │   ├─ is_allowed(sender_id) 权限检查                         │
│    │   ├─ group_policy 群组策略检查                               │
│    │   ├─ 下载附件 → ~/.nanobot/data/media/discord/              │
│    │   └─ _handle_message() → InboundMessage                    │
│    ├─ op=7 RECONNECT → 重连                                     │
│    └─ op=9 INVALID_SESSION → 重连                               │
│                                                                 │
│  send():                                                        │
│    ├─ 发送媒体文件（multipart/form-data）                         │
│    ├─ 分割文本（≤2000 字符/条）                                   │
│    └─ 429 速率限制处理（重试 3 次）                                │
│                                                                 │
│  _start_typing():                                               │
│    └─ 每 8 秒 POST typing endpoint                              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                    InboundMessage / OutboundMessage
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                      MessageBus                                  │
│                                                                 │
│  inbound:  asyncio.Queue  ←── DiscordChannel / SlackChannel ... │
│  outbound: asyncio.Queue  ──→ ChannelManager._dispatch_outbound │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                      AgentLoop                                   │
│                                                                 │
│  _dispatch(msg):                                                │
│    ├─ 获取 _processing_lock（全局锁，串行处理）                    │
│    ├─ session = SessionManager.get(msg.session_key)             │
│    ├─ ContextBuilder.build_messages(session, msg)               │
│    │   └─ [system_prompt, *history, user_msg]                   │
│    ├─ _run_agent_loop() — 最多 40 轮 LLM + tool 执行            │
│    ├─ _save_turn() — 清洗并持久化消息                             │
│    └─ 发布 OutboundMessage 到 bus.outbound                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. 与其他平台实现对比

| 特性          | Discord                   | Slack                    | Telegram                  |
| ------------- | ------------------------- | ------------------------ | ------------------------- |
| 连接方式      | Gateway WebSocket（原生） | Socket Mode（Slack SDK） | HTTP 长轮询               |
| 消息长度限制  | 2000 字符                 | 40000 字符               | 4096 字符                 |
| 线程/话题隔离 | ❌                        | ✅ thread_ts             | ✅ topic_id               |
| Typing 指示器 | ✅ 每 8 秒心跳            | ✅                       | ✅                        |
| 引用消息      | ✅ reply_to               | ✅ thread_ts             | ✅ reply_to_message_id    |
| 附件大小限制  | 20MB（代码限制）          | 依赖 Slack 配置          | 20MB（Telegram API 限制） |
| 群组策略      | mention / open            | mention / open           | mention / open            |
| 媒体组        | ❌                        | ❌                       | ✅ media_group            |
| 重连机制      | ✅ 5 秒延迟自动重连       | 依赖 SDK                 | ✅ 轮询自动恢复           |
| 速率限制处理  | ✅ 429 + retry_after      | 依赖 SDK                 | ✅ 429 + retry_after      |

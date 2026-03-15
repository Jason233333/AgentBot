# Agent 间无法通信

> 项目：nanobot (HKUDS/nanobot)
> 代码版本：0.1.4.post4: 65cbd7eb78672e226a8108c81da3ed8ce50ab192
> 涉及文件：
>
> - `nanobot/channels/discord.py`（bot 消息过滤逻辑）
> - `nanobot/agent/subagent.py`（subagent 无 spawn/message 工具）
> - `nanobot/agent/tools/spawn.py`（spawn 工具，不对 subagent 开放）

---

## 问题描述

当前架构下，多个 nanobot agent 实例之间没有任何通信机制。这个限制存在于三个层面：

### 层面一：平台层过滤

Discord/Slack/Telegram 的 channel 实现均显式过滤 bot 消息：

```python
# discord.py — 消息处理时
if author.get("bot"):
    return  # 忽略所有 bot 消息
```

即使两个 nanobot 实例共处同一个 Discord 频道，Agent A 发的消息会被 Agent B 直接丢弃。这是防止 bot 无限循环对话的安全设计，但也彻底阻断了通过平台中转的 agent 间通信。

### 层面二：subagent 能力受限

subagent 被剥夺了 `spawn` 和 `message` 工具（7 个工具 vs 主 agent 的 10 个），意味着：

- subagent 不能创建新的 subagent（禁止嵌套）
- subagent 不能向任何频道发送消息
- subagent 只能通过返回结果（`_announce_result`）与主 agent 单向通信

### 层面三：无跨实例通信协议

不同 nanobot 进程之间没有任何 IPC、消息队列或共享状态机制。每个实例是完全独立的：独立的 MessageBus、独立的 Session 存储、独立的 MEMORY.md。

### 影响

| 场景          | 限制                                                          |
| ------------- | ------------------------------------------------------------- |
| 多 agent 协作 | 无法让"研究 agent"查资料后通知"写作 agent"，需要人工中转      |
| 任务分发      | 无法实现 orchestrator 模式（一个 agent 分配任务给多个 agent） |
| 专业化分工    | 不同领域的 agent 无法互相请求帮助                             |
| 人在环路外    | 多 agent 场景必须有人工参与转发结果                           |

### 当前设计的合理性

- 防止 bot 循环对话（两个 bot 互相回复导致无限循环）是合理的安全设计
- subagent 限制防止资源耗尽（无限嵌套 spawn）
- 单实例架构降低复杂度，适合个人/小团队使用场景

---

## 改进建议

### 方案 A：内部 Agent 总线（推荐）

在同一个 nanobot 进程内支持多 agent，通过内部 MessageBus 通信：

```python
# 伪代码
class AgentBus:
    """Agent 间通信总线，与外部 MessageBus 分离"""

    async def send(self, from_agent: str, to_agent: str, message: str):
        """Agent A 向 Agent B 发送消息"""
        msg = InboundMessage(
            channel="agent",
            sender_id=from_agent,
            chat_id=to_agent,
            content=message,
        )
        target_loop = self.agent_registry[to_agent]
        await target_loop.bus.publish_inbound(msg)
```

需要配套：

- Agent 注册表（name → AgentLoop 映射）
- 循环检测机制（防止 A→B→A 无限循环，可用消息深度计数器或 TTL）
- 新工具 `send_to_agent(agent_name, message)` 供 agent 调用

**优势**：单进程内实现，延迟低，共享资源
**代价**：架构改动较大；需要解决循环对话和资源竞争

### 方案 B：外部消息队列

通过 Redis Pub/Sub、NATS 或类似中间件实现跨实例通信：

```
Agent A (nanobot 实例 1) → Redis → Agent B (nanobot 实例 2)
```

需要：

- 新增一个 `RedisChannel`（实现 BaseChannel 接口）
- 约定消息格式和路由规则
- 实现消息去重和循环检测

**优势**：跨进程/跨机器通信；与现有架构耦合度低
**代价**：引入外部依赖；网络延迟；部署复杂度增加

### 方案 C：共享文件协议（最小改动）

利用现有 `read_file` / `write_file` 工具，约定一个共享目录作为"信箱"：

```
~/.nanobot/shared/mailbox/
    agent_a_to_agent_b/
        2026-03-14T10:00:00.md   # Agent A 写入的消息
    agent_b_to_agent_a/
        2026-03-14T10:00:05.md   # Agent B 的回复
```

Agent 通过 heartbeat 定期检查信箱目录，发现新文件则读取并处理。

**优势**：零代码改动，利用现有工具和 heartbeat 机制
**代价**：延迟高（取决于 heartbeat 间隔）；无实时性；需要人工或 prompt 约定协议

### 方案 D：放宽 bot 消息过滤（最小改动，慎用）

在 channel 配置中增加白名单，允许特定 bot ID 的消息通过：

```python
# discord.py
allowed_bots = getattr(self.config, "allow_bots", [])
if author.get("bot") and str(author["id"]) not in allowed_bots:
    return

# 配置
channels:
  discord:
    allow_bots: ["bot_id_of_agent_b"]  # 只允许特定 bot
```

**优势**：最小改动，agent 通过 Discord 频道自然通信
**代价**：必须配套循环检测（消息深度/TTL），否则两个 bot 会无限对话；依赖外部平台中转，延迟高

### 推荐

短期用 **方案 C**（共享文件）做简单的异步协作。中期实现 **方案 A**（内部 Agent 总线）支持同进程多 agent 协作。方案 B 适合需要跨机器部署的场景。方案 D 风险较高，不建议作为首选。

---

## 关联问题

- **全局处理锁**（见 `nanobot-global-processing-lock.md`）：即使实现了 agent 间通信，全局锁仍会导致消息串行处理，需要配合会话级锁改造
- **subagent 能力受限**：如果实现了 agent 间通信，可以考虑放宽 subagent 的工具限制，或将 subagent 升级为完整 agent

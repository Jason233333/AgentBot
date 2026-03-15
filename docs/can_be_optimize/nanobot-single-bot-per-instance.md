# 单实例仅支持单个同类 Channel Bot

> 项目：nanobot (HKUDS/nanobot)
> 代码版本：0.1.4.post4: 65cbd7eb78672e226a8108c81da3ed8ce50ab192
> 涉及文件：
>
> - `nanobot/config/schema.py:214-230`（ChannelsConfig 定义）
> - `nanobot/channels/manager.py:25-52`（\_init_channels 初始化）

---

## 问题描述

`ChannelsConfig` 中每种频道类型只有一个配置对象，不是列表：

```python
# schema.py:214-230
class ChannelsConfig(Base):
    discord: DiscordConfig = Field(default_factory=DiscordConfig)    # 单个对象
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)  # 单个对象
    slack: SlackConfig = Field(default_factory=SlackConfig)          # 单个对象
    # ...
```

这意味着**一个 nanobot 实例只能运行一个 Discord bot、一个 Telegram bot、一个 Slack bot**，无法在同一实例中配置多个同类 bot。

### 影响

| 场景       | 限制                                                        |
| ---------- | ----------------------------------------------------------- |
| 多人格 bot | 想在不同服务器部署不同人格的 bot，必须启动多个 nanobot 进程 |
| 多租户     | 无法为不同用户/团队提供独立的 bot 实例                      |
| A/B 测试   | 无法在同一进程内运行两个不同模型的 bot 做对比               |
| 运维成本   | 每多一个 bot 就需要多一个进程、多一份配置文件、多一个工作区 |

### 当前 workaround

运行多个 nanobot 进程，各自使用独立的配置文件和工作区：

```bash
nanobot gateway -c ~/.nanobot/config-bot-a.json -w ~/.nanobot/workspace-bot-a
nanobot gateway -c ~/.nanobot/config-bot-b.json -w ~/.nanobot/workspace-bot-b
```

可行但存在以下不足：

- 进程数线性增长，资源占用翻倍（每个进程独立加载 LLM provider、Channel 连接等）
- 多个 bot 之间无法共享记忆或通信（参见 `nanobot-no-inter-agent-communication.md`）
- 管理复杂度随 bot 数量增长

---

## 改进建议

### 方案 A：Channel 配置改为列表（推荐）

将 `ChannelsConfig` 中的单一对象改为命名字典或列表：

```python
# 改造后
class ChannelsConfig(Base):
    discord: dict[str, DiscordConfig] = Field(default_factory=dict)
    # ...
```

对应配置：

```json
{
  "channels": {
    "discord": {
      "bot_helper": {
        "enabled": true,
        "token": "TOKEN_A",
        "allowFrom": ["*"],
        "groupPolicy": "mention"
      },
      "bot_coder": {
        "enabled": true,
        "token": "TOKEN_B",
        "allowFrom": ["123456"],
        "groupPolicy": "open"
      }
    }
  }
}
```

需要配套修改：

- `ChannelManager._init_channels()` 遍历字典而非单一对象
- `session_key` 格式从 `discord:{chat_id}` 扩展为 `discord:{bot_name}:{chat_id}` 以隔离会话
- 出站路由需要区分目标 bot

**优势**：单进程管理多个 bot，共享 LLM provider 连接和内存
**代价**：配置 schema 不向后兼容，需要迁移逻辑

### 方案 B：多 Agent 架构

结合 `nanobot-no-inter-agent-communication.md` 中的方案 A（内部 Agent 总线），实现单进程多 Agent，每个 Agent 绑定不同的 Channel 实例：

```
nanobot 进程
├── Agent A (helper) ── Discord Bot A
├── Agent B (coder)  ── Discord Bot B
└── Agent Bus（内部通信）
```

**优势**：不仅支持多 bot，还支持 agent 间协作
**代价**：架构改动最大

### 推荐

短期用 **多进程 workaround**。中期实现 **方案 A**（Channel 列表化），这是最小且最实用的改动。长期如果需要 agent 协作，再推进方案 B。

---

## 关联问题

- `nanobot-no-inter-agent-communication.md` — 多 bot 之间无法通信
- `nanobot-global-processing-lock.md` — 即使支持多 bot，全局锁仍会导致所有 bot 的消息串行处理

# nanobot 快速上手指南

> 项目：nanobot (HKUDS/nanobot)
> 代码版本：0.1.4.post4

---

## 1. 安装

```bash
pip install nanobot-ai
```

验证安装：

```bash
nanobot --version
```

---

## 2. 初始化

```bash
nanobot onboard
```

该命令会：

1. 在 `~/.nanobot/config.json` 创建默认配置文件（已存在则提供覆盖选项）
2. 在 `~/.nanobot/workspace/` 创建工作区，同步以下模板文件（仅创建缺失文件，不覆盖已有）：

```
~/.nanobot/
├── config.json                # 主配置文件
└── workspace/
    ├── SOUL.md                # Bot 人格与价值观定义
    ├── USER.md                # 用户信息与偏好
    ├── AGENTS.md              # 代理指令与任务建议
    ├── TOOLS.md               # 工具使用说明
    ├── HEARTBEAT.md           # 定期任务列表
    ├── memory/
    │   ├── MEMORY.md          # 长期记忆（注入 system prompt）
    │   └── HISTORY.md         # 历史摘要（grep 可检索）
    └── skills/                # 用户自定义技能目录
```

运行时还会产生以下数据目录：

```
~/.nanobot/
├── history/cli_history        # CLI 命令历史
├── media/                     # 各频道下载的媒体文件
│   ├── discord/
│   ├── telegram/
│   └── ...
├── cron/jobs.json             # 计划任务
└── logs/                      # 运行日志
```

---

## 3. 配置文件详解

配置文件位于 `~/.nanobot/config.json`，格式为 JSON。所有字段同时支持 `camelCase` 和 `snake_case`。

### 3.1 环境变量覆盖

所有配置项都可以通过环境变量覆盖，规则：

- 前缀：`NANOBOT_`
- 嵌套用 `__`（双下划线）分隔

```bash
# 等同于 config.json 中的 providers.openrouter.apiKey
export NANOBOT_PROVIDERS__OPENROUTER__API_KEY="sk-or-v1-xxx"

# 等同于 channels.discord.token
export NANOBOT_CHANNELS__DISCORD__TOKEN="your-bot-token"
```

### 3.2 完整配置结构

```jsonc
{
  // ── 代理配置 ──
  "agents": {
    "defaults": {
      "workspace": "~/.nanobot/workspace", // 工作区路径
      "model": "anthropic/claude-opus-4-5", // 默认模型
      "provider": "auto", // LLM 提供商（"auto" 自动匹配）
      "maxTokens": 8192, // 单次生成最大 token 数
      "contextWindowTokens": 65536, // 上下文窗口大小
      "temperature": 0.1, // 生成温度
      "maxToolIterations": 40, // 单次请求最多 tool 调用轮数
      "reasoningEffort": null, // thinking 模式："low"/"medium"/"high"/null
    },
  },

  // ── LLM 提供商 ──
  "providers": {
    "<provider_name>": {
      "apiKey": "", // API 密钥
      "apiBase": null, // 自定义端点 URL
      "extraHeaders": {}, // 自定义请求头
    },
  },

  // ── 频道配置 ──
  "channels": {
    "sendProgress": true, // 是否发送进度消息
    "sendToolHints": false, // 是否发送工具调用提示
    "discord": {
      /* 见第 4 节 */
    },
    "telegram": {
      /* ... */
    },
    "slack": {
      /* ... */
    },
    // ...更多频道
  },

  // ── 网关配置 ──
  "gateway": {
    "host": "0.0.0.0", // 监听地址
    "port": 18790, // 监听端口
    "heartbeat": {
      "enabled": true, // 是否启用定期心跳任务
      "intervalS": 1800, // 心跳间隔（秒），默认 30 分钟
    },
  },

  // ── 工具配置 ──
  "tools": {
    "restrictToWorkspace": false, // 是否限制文件操作在 workspace 内
    "web": {
      "proxy": null, // HTTP 代理
      "search": {
        "provider": "brave", // 搜索引擎：brave/tavily/duckduckgo/searxng/jina
        "apiKey": "", // 搜索 API 密钥
        "maxResults": 5, // 搜索结果数量
      },
    },
    "exec": {
      "timeout": 60, // 命令执行超时（秒）
      "pathAppend": "", // 追加到 PATH
    },
    "mcpServers": {}, // MCP 服务器配置
  },
}
```

### 3.3 支持的 LLM 提供商

| 提供商        | provider 名    | 说明                                    |
| ------------- | -------------- | --------------------------------------- |
| Anthropic     | `anthropic`    | Claude 系列                             |
| OpenAI        | `openai`       | GPT 系列                                |
| OpenRouter    | `openrouter`   | 多模型网关（推荐，一个 key 用所有模型） |
| Azure OpenAI  | `azure_openai` | Azure 托管                              |
| DeepSeek      | `deepseek`     | DeepSeek 系列                           |
| Groq          | `groq`         | 快速推理                                |
| Google Gemini | `gemini`       | Gemini 系列                             |
| Ollama        | `ollama`       | 本地模型                                |
| vLLM          | `vllm`         | 本地兼容服务器                          |
| 其他          | `custom`       | 任何 OpenAI 兼容端点                    |

还支持：`minimax`、`moonshot`、`zhipu`、`dashscope`、`siliconflow`、`volcengine`、`byteplus`、`aihubmix`、`openai_codex`、`github_copilot`

### 3.4 支持的频道

| 频道     | 配置键     | 连接方式                  |
| -------- | ---------- | ------------------------- |
| Discord  | `discord`  | Gateway WebSocket（原生） |
| Telegram | `telegram` | HTTP 长轮询               |
| Slack    | `slack`    | Socket Mode（Slack SDK）  |
| WhatsApp | `whatsapp` | Bridge 桥接               |
| 飞书     | `feishu`   | Webhook                   |
| 钉钉     | `dingtalk` | Webhook                   |
| 企业微信 | `wecom`    | Webhook                   |
| QQ       | `qq`       | 待定                      |
| Matrix   | `matrix`   | Matrix SDK                |
| Email    | `email`    | IMAP/SMTP                 |
| MoChat   | `mochat`   | 自定义                    |

所有频道都有三个通用字段：

```jsonc
{
  "enabled": false, // 是否启用
  "allowFrom": [], // 允许的用户 ID 列表，"*" 表示全部允许
  "groupPolicy": "mention", // "mention"=仅 @bot 时响应，"open"=响应所有消息
}
```

---

## 4. Discord Bot 配置

### 4.1 在 Discord Developer Portal 创建 Bot

**第一步：创建 Application**

1. 打开 https://discord.com/developers/applications
2. 点击 **New Application** → 输入名称 → 创建

**第二步：创建 Bot 并获取 Token**

1. 左侧菜单选择 **Bot**
2. 点击 **Reset Token** → 复制 Token（只显示一次，务必保存）

**第三步：启用必要的 Intents**

在 Bot 页面下方 **Privileged Gateway Intents** 区域，启用：

- ✅ **MESSAGE CONTENT INTENT**（必须，否则收不到消息内容）
- 可选：**SERVER MEMBERS INTENT**

> nanobot 默认 intents 值为 `37377`，对应 GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT。

**第四步：邀请 Bot 到服务器**

1. 左侧菜单选择 **OAuth2** → **URL Generator**
2. Scopes 勾选 **bot**
3. Bot Permissions 勾选：
   - ✅ Send Messages
   - ✅ Read Message History
   - ✅ Attach Files（如果需要发送文件）
4. 复制生成的 URL，在浏览器中打开，选择服务器并授权

**第五步：获取你的 User ID**

1. 打开 Discord → Settings → Advanced → 启用 **Developer Mode**
2. 右键你的头像 → **Copy User ID**

### 4.2 配置 nanobot

编辑 `~/.nanobot/config.json`：

```json
{
  "channels": {
    "discord": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"],
      "groupPolicy": "mention"
    }
  },
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-5"
    }
  }
}
```

或使用环境变量（不把 token 写入文件）：

```bash
export NANOBOT_CHANNELS__DISCORD__TOKEN="YOUR_BOT_TOKEN"
export NANOBOT_PROVIDERS__OPENROUTER__API_KEY="sk-or-v1-xxx"
```

### 4.3 Discord 配置字段一览

| 字段          | 类型      | 默认值                                         | 说明                                                  |
| ------------- | --------- | ---------------------------------------------- | ----------------------------------------------------- |
| `enabled`     | bool      | `false`                                        | 是否启用                                              |
| `token`       | str       | `""`                                           | Bot Token                                             |
| `allowFrom`   | list[str] | `[]`                                           | 允许的用户 ID 列表，`"*"` 允许所有人                  |
| `gatewayUrl`  | str       | `wss://gateway.discord.gg/?v=10&encoding=json` | Gateway 地址，一般不需要改                            |
| `intents`     | int       | `37377`                                        | 权限位，一般不需要改                                  |
| `groupPolicy` | str       | `"mention"`                                    | `"mention"` = 仅 @bot 时响应；`"open"` = 响应所有消息 |

### 4.4 启动并验证

```bash
nanobot gateway
```

启动后你会在终端看到连接日志。在 Discord 中：

- **服务器频道**：@mention Bot 发送消息（group_policy 为 mention 时）
- **私信 DM**：直接发消息

Bot 应该会显示"正在输入…"然后回复。

### 4.5 常见问题排查

**启动后提示没有 Discord / 频道未加载：**

- 检查 `enabled` 是否为 `true`。`nanobot onboard` 生成的默认配置中所有频道都是 `"enabled": false`，必须手动改为 `true`。

**Bot 在线但不回复消息：**

- `allowFrom` 必须填 **Discord 数字 User ID**（如 `"283746501928374650"`），不是用户名。获取方式：Discord Settings → Advanced → 启用 Developer Mode → 右键头像 → Copy User ID。
- `allowFrom` 为空列表时系统会拒绝所有消息并退出报错——至少填一个 User ID 或 `"*"`。
- 如果在服务器频道中使用，`groupPolicy` 为 `"mention"` 时必须 @bot 才会响应。

**连接失败 / Token 无效：**

- 确认 Bot Token 正确且未过期。如果泄露过，去 Discord Developer Portal 点 **Reset Token** 重新生成。
- 确认在 Bot 设置中启用了 **MESSAGE CONTENT INTENT**。

**安全建议：**

- 不要将 Bot Token 和 API Key 提交到 Git。建议使用环境变量：
  ```bash
  export NANOBOT_CHANNELS__DISCORD__TOKEN="YOUR_BOT_TOKEN"
  export NANOBOT_PROVIDERS__ANTHROPIC__API_KEY="sk-ant-xxx"
  ```
- 如果 Token 或 API Key 曾经泄露（如出现在日志、聊天记录中），立即轮换。

### 4.6 其他注意事项

- 附件大小限制 20MB，超过的文件会被跳过
- 单条回复超过 2000 字符会自动分割成多条消息
- Bot 不会响应其他 Bot 的消息（防止循环对话）

---

## 5. CLI 命令参考

### 5.1 核心命令

| 命令                       | 说明                                         |
| -------------------------- | -------------------------------------------- |
| `nanobot onboard`          | 初始化配置文件和工作区                       |
| `nanobot gateway`          | 启动网关（监听所有已启用的频道 + heartbeat） |
| `nanobot agent`            | 启动交互式终端聊天                           |
| `nanobot status`           | 查看运行状态                                 |
| `nanobot --version` / `-v` | 显示版本号                                   |

### 5.2 `nanobot gateway` 选项

```bash
nanobot gateway [OPTIONS]
```

| 选项                     | 说明         | 默认值                   |
| ------------------------ | ------------ | ------------------------ |
| `-p`, `--port PORT`      | 监听端口     | 18790                    |
| `-w`, `--workspace PATH` | 工作区路径   | `~/.nanobot/workspace`   |
| `-c`, `--config PATH`    | 配置文件路径 | `~/.nanobot/config.json` |
| `-v`, `--verbose`        | 详细日志     | false                    |

gateway 模式下会同时启动：

- 所有已启用的 Channel（Discord、Telegram 等）
- AgentLoop（消息处理循环）
- HeartbeatService（定期任务，如果配置启用）
- ChannelManager 出站消息分发

### 5.3 `nanobot agent` 选项

```bash
nanobot agent [OPTIONS]
```

| 选项                           | 说明                             | 默认值                   |
| ------------------------------ | -------------------------------- | ------------------------ |
| `-m`, `--message TEXT`         | 发送单条消息后退出（非交互模式） | 无                       |
| `-s`, `--session ID`           | 指定会话 ID                      | `cli:direct`             |
| `-w`, `--workspace PATH`       | 工作区路径                       | `~/.nanobot/workspace`   |
| `-c`, `--config PATH`          | 配置文件路径                     | `~/.nanobot/config.json` |
| `--markdown` / `--no-markdown` | 是否渲染 Markdown                | true                     |
| `--logs` / `--no-logs`         | 是否显示运行时日志               | false                    |

交互模式下可用的退出命令：`exit`、`quit`、`/exit`、`/quit`、`:q`、`Ctrl+D`

交互模式下的内置命令：

- `/new` — 清空当前 session，开始新对话
- `/stop` — 停止当前正在运行的所有 subagent
- `/help` — 显示帮助

### 5.4 频道管理命令

```bash
nanobot channels status          # 查看各频道连接状态
nanobot channels login           # WhatsApp QR 码登录
```

### 5.5 OAuth 登录

```bash
nanobot provider login openai-codex      # OpenAI Codex OAuth
nanobot provider login github-copilot    # GitHub Copilot OAuth
```

---

## 6. 最小可运行配置示例

### Discord + OpenRouter（推荐入门）

```json
{
  "channels": {
    "discord": {
      "enabled": true,
      "token": "YOUR_DISCORD_BOT_TOKEN",
      "allowFrom": ["YOUR_DISCORD_USER_ID"],
      "groupPolicy": "mention"
    }
  },
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-5"
    }
  }
}
```

### Telegram + Anthropic 直连

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_TELEGRAM_BOT_TOKEN",
      "allowFrom": ["YOUR_TELEGRAM_USER_ID"]
    }
  },
  "providers": {
    "anthropic": {
      "apiKey": "sk-ant-xxx"
    }
  },
  "agents": {
    "defaults": {
      "model": "claude-opus-4-5-20250514"
    }
  }
}
```

### 本地模型（Ollama）

```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434"
    }
  },
  "agents": {
    "defaults": {
      "model": "ollama/llama3",
      "provider": "ollama"
    }
  }
}
```

启动后用 `nanobot agent` 在终端交互，无需配置任何频道。


### 启动 Discord bot 实例

可按照 `.nanobot.example` 配置后，按照下面的方式启动 `bot` 实例（如果有多个，则需要启动多个 bot 实例）。

```
nanobot gateway --config .nanobot/.bot-chat/config.json --workspace .nanobot/.bot-chat/workspace
```
# Nanobot 源码架构分析

## 一、项目概览

**Nanobot** 是一个轻量级个人 AI 助手框架，用 Python 编写，灵感来自 OpenClaw。它以约 500 行核心代码实现了完整的智能代理功能，支持多 LLM 提供商和多聊天平台。

- **版本**: 0.1.4.post4
- **Python**: >=3.11
- **核心依赖**: litellm, pydantic, asyncio, websockets
- **许可证**: MIT
- **开源地址**: GitHub HKUDS/nanobot

---

## 二、设计思想

### 2.1 消息驱动 + 异步优先

整个架构围绕一个核心理念：**用异步消息队列解耦聊天渠道和智能代理**。`MessageBus` 是系统的中枢——渠道负责收发消息，代理负责思考和行动，两者通过 `inbound`/`outbound` 两个 `asyncio.Queue` 通信，互不感知对方的实现细节。

```
用户 → [Telegram/Discord/...] → inbound queue → AgentLoop → LLM → outbound queue → [Channel] → 用户
```

这使得新增一个聊天平台只需实现 `BaseChannel` 接口，完全不碰代理逻辑。

### 2.2 插件式工具系统

工具（Tools）是代理与外部世界交互的唯一途径。所有工具继承自 `Tool` 抽象基类，通过 `ToolRegistry` 动态注册。这套设计：

- **统一接口**：每个工具暴露 `name`/`description`/`parameters`（JSON Schema）/`execute()`
- **自动验证**：`Tool` 基类内置了 JSON Schema 类型转换和参数校验
- **运行时可扩展**：MCP 工具在首次使用时懒加载注册

### 2.3 提供商抽象

通过 `LLMProvider` 抽象基类统一所有 LLM 的调用接口。主要实现 `LiteLLMProvider` 利用 litellm 库支持 50+ 家 LLM 提供商，配合 `registry.py` 的提供商元数据注册表实现**零 if-elif** 的提供商适配。

### 2.4 两层记忆系统

记忆设计是 nanobot 的亮点之一：

- **MEMORY.md**（长期记忆）：结构化的 Markdown，存储关键事实，直接注入系统提示
- **HISTORY.md**（历史日志）：每条以 `[YYYY-MM-DD HH:MM]` 开头，支持 grep 搜索

巩固策略是 **token 驱动**的：当会话的 prompt token 数超过上下文窗口大小时，自动调用 LLM 总结旧消息并归档。

### 2.5 渐进式技能加载

技能（Skills）是 Markdown 文件，以摘要形式列在系统提示中，代理需要时再用 `read_file` 工具读取完整内容。这避免了系统提示过长。`always=true` 的技能会直接注入。

---

## 三、项目结构

```
nanobot/
├── nanobot/                     # Python 主包
│   ├── __init__.py
│   ├── __main__.py              # 模块入口 (python -m nanobot)
│   ├── cli/                     # CLI 命令处理
│   │   └── commands.py          # typer 命令定义 (onboard/agent/gateway/status)
│   ├── agent/                   # 核心智能代理引擎
│   │   ├── loop.py              # AgentLoop - 核心处理循环
│   │   ├── context.py           # ContextBuilder - 系统提示 + 消息组装
│   │   ├── memory.py            # MemoryStore + MemoryConsolidator
│   │   ├── skills.py            # SkillsLoader - 技能加载
│   │   ├── subagent.py          # SubagentManager - 后台任务
│   │   └── tools/               # 工具系统
│   │       ├── base.py          # Tool 抽象基类
│   │       ├── registry.py      # ToolRegistry
│   │       ├── filesystem.py    # 文件操作工具
│   │       ├── shell.py         # ExecTool
│   │       ├── web.py           # WebSearch + WebFetch
│   │       ├── message.py       # MessageTool
│   │       ├── spawn.py         # SpawnTool
│   │       ├── cron.py          # CronTool
│   │       └── mcp.py           # MCP 工具包装器
│   ├── config/                  # 配置管理
│   │   ├── schema.py            # Pydantic v2 配置模型
│   │   ├── loader.py            # JSON 加载/保存
│   │   └── paths.py             # 路径常量
│   ├── providers/               # LLM 供应商集成
│   │   ├── base.py              # LLMProvider 抽象基类
│   │   ├── litellm_provider.py  # LiteLLM 多供应商实现
│   │   ├── registry.py          # 供应商元数据注册表
│   │   ├── custom_provider.py   # 自定义 OpenAI 兼容端点
│   │   ├── azure_openai_provider.py
│   │   └── openai_codex_provider.py
│   ├── channels/                # 聊天平台适配器
│   │   ├── base.py              # BaseChannel 抽象基类
│   │   ├── manager.py           # ChannelManager
│   │   ├── registry.py          # 渠道自动发现
│   │   ├── telegram.py
│   │   ├── discord.py
│   │   ├── whatsapp.py
│   │   ├── feishu.py
│   │   ├── dingtalk.py
│   │   ├── email.py
│   │   ├── matrix.py
│   │   └── slack.py
│   ├── bus/                     # 消息队列系统
│   │   ├── events.py            # InboundMessage / OutboundMessage
│   │   └── queue.py             # MessageBus
│   ├── session/                 # 会话管理
│   │   └── manager.py           # Session + SessionManager
│   ├── cron/                    # 定时任务服务
│   │   └── service.py           # CronService
│   ├── heartbeat/               # 心跳监控服务
│   │   └── service.py           # HeartbeatService
│   ├── skills/                  # 内置技能模块
│   ├── templates/               # 工作空间模板文件
│   └── utils/                   # 工具函数
│       └── helpers.py
├── bridge/                      # WhatsApp Baileys 网桥 (Node.js/TypeScript)
├── tests/                       # 测试套件
└── pyproject.toml               # 项目配置
```

---

## 四、完整生命周期

以下以 **gateway 模式 + Telegram 渠道** 为例，完整追踪从进程启动到用户收到回复的每一步。

### 阶段一：进程启动（`nanobot gateway`）

```
用户执行 `nanobot gateway`
        │
        ▼
cli/commands.py: gateway()
        │
        ├─ 1. _load_runtime_config()
        │     加载 ~/.nanobot/config.json → Pydantic Config 对象
        │     解析 providers / channels / tools / agents 配置
        │
        ├─ 2. sync_workspace_templates()
        │     同步内置模板文件到 workspace (AGENTS.md, SOUL.md 等)
        │
        ├─ 3. MessageBus()
        │     创建 inbound/outbound 两个 asyncio.Queue
        │
        ├─ 4. _make_provider(config)
        │     根据模型名 → registry 查找供应商 → 实例化 LiteLLMProvider
        │     设置 GenerationSettings (temperature, max_tokens, reasoning_effort)
        │     配置环境变量 (API key, base URL)
        │
        ├─ 5. SessionManager(workspace)
        │     初始化会话目录 workspace/sessions/
        │
        ├─ 6. CronService(jobs.json)
        │     加载持久化的定时任务
        │
        ├─ 7. AgentLoop(bus, provider, workspace, ...)
        │     ├─ ContextBuilder(workspace)
        │     │    ├─ MemoryStore(workspace) → 读取 MEMORY.md
        │     │    └─ SkillsLoader(workspace) → 扫描技能目录
        │     ├─ ToolRegistry()
        │     │    └─ _register_default_tools()
        │     │         注册 9 个内置工具:
        │     │         read_file, write_file, edit_file, list_dir,
        │     │         exec, web_search, web_fetch, message, spawn
        │     │         (如果有 cron_service 则注册 cron 工具)
        │     ├─ SubagentManager(...)
        │     └─ MemoryConsolidator(...)
        │
        ├─ 8. ChannelManager(config, bus)
        │     ├─ pkgutil 扫描 channels/ 目录发现所有渠道模块
        │     ├─ 检查 config.channels.{name}.enabled
        │     ├─ 实例化启用的渠道 (如 TelegramChannel)
        │     ├─ 设置 transcription_api_key (Groq)
        │     └─ _validate_allow_from() → 空 allow_from 直接 SystemExit
        │
        ├─ 9. HeartbeatService(...)
        │     配置定期唤醒间隔
        │
        └─ 10. asyncio.run(run())
              并发启动三个长驻任务:
              ├─ cron.start()           → 定时任务调度循环
              ├─ heartbeat.start()      → 心跳检查循环
              └─ asyncio.gather(
                   agent.run(),         → 代理主循环 (消费 inbound)
                   channels.start_all() → 所有渠道监听 + outbound 分发
                 )
```

**关键组件就绪后的运行状态**：

```
┌────────────────────────────────────────────────────────────┐
│                    asyncio event loop                       │
│                                                            │
│  [TelegramChannel.start()]  ←── 长连接监听 Telegram API    │
│  [DiscordChannel.start()]   ←── WebSocket 监听 Discord     │
│  [ChannelManager._dispatch_outbound()]  ←── 消费 outbound  │
│  [AgentLoop.run()]          ←── 消费 inbound               │
│  [CronService._loop()]     ←── 定时任务调度                │
│  [HeartbeatService._loop()] ←── 心跳检查                   │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 阶段二：用户发送消息

```
用户在 Telegram 发送 "帮我搜索 Python 异步编程的最佳实践"
        │
        ▼
Telegram API → python-telegram-bot 回调
        │
        ▼
TelegramChannel._handle_message(sender_id, chat_id, content)
        │
        ├─ 1. is_allowed(sender_id)
        │     检查 sender_id 是否在 allow_from 列表中
        │     如果不在 → logger.warning + 静默丢弃，不回复
        │
        ├─ 2. 构造 InboundMessage
        │     InboundMessage(
        │       channel="telegram",
        │       sender_id="123456",
        │       chat_id="789",
        │       content="帮我搜索 Python 异步编程的最佳实践",
        │       media=[],
        │       metadata={message_id: "xxx"},
        │     )
        │     session_key 属性 → "telegram:789"
        │
        └─ 3. await bus.publish_inbound(msg)
              消息进入 inbound asyncio.Queue
```

### 阶段三：代理处理消息

```
AgentLoop.run() 从 inbound 队列取出消息
        │
        ▼
asyncio.create_task(_dispatch(msg))
        │  每条消息创建独立 task，支持 /stop 取消
        │  task 注册到 _active_tasks[session_key]
        │
        ▼
_dispatch(msg)
        │  → async with _session_lock[session_key]  (per-session 锁，同 session 串行)
        │  → async with _concurrency_sem            (全局 Semaphore，最多 32 并发)
        │
        ▼
_process_message(msg)
        │
        ├─ 1. 斜杠命令检查
        │     /new  → 归档当前会话 → 清空 → 返回 "New session started."
        │     /help → 返回帮助信息
        │     /stop, /restart 在 run() 层已处理
        │
        ├─ 2. session = sessions.get_or_create("telegram:789")
        │     ├─ 检查内存缓存
        │     ├─ 从磁盘加载 JSONL (sessions/telegram_789.jsonl)
        │     │   第一行: metadata (key, created_at, last_consolidated)
        │     │   后续行: 每行一条消息 JSON
        │     └─ 如果不存在 → 创建新 Session
        │
        ├─ 3. memory_consolidator.maybe_consolidate_by_tokens(session)
        │     估算当前 prompt token 数
        │     如果超过 context_window_tokens → 触发巩固
        │     (详见阶段六)
        │
        ├─ 4. _set_tool_context("telegram", "789", message_id)
        │     更新 message/spawn/cron 工具的渠道路由信息
        │
        ├─ 5. history = session.get_history(max_messages=0)
        │     返回 last_consolidated 之后的所有消息
        │     从第一条 role="user" 开始对齐，避免孤立 tool_result
        │
        ├─ 6. initial_messages = context.build_messages(history, content)
        │     │
        │     ├─ build_system_prompt()
        │     │   组装系统提示:
        │     │   ┌──────────────────────────────────────────┐
        │     │   │ # nanobot 🐈                             │
        │     │   │ Identity + Runtime + Workspace + 行为准则│
        │     │   │ ---                                      │
        │     │   │ ## AGENTS.md / SOUL.md / USER.md 等      │
        │     │   │ ---                                      │
        │     │   │ # Memory                                 │
        │     │   │ ## Long-term Memory                      │
        │     │   │ (MEMORY.md 内容)                         │
        │     │   │ ---                                      │
        │     │   │ # Active Skills                          │
        │     │   │ (always=true 的技能全文)                  │
        │     │   │ ---                                      │
        │     │   │ # Skills                                 │
        │     │   │ <skills>                                 │
        │     │   │   <skill available="true">               │
        │     │   │     <name>weather</name>                 │
        │     │   │     <description>...</description>       │
        │     │   │     <location>/.../SKILL.md</location>   │
        │     │   │   </skill>                               │
        │     │   │   ...                                    │
        │     │   │ </skills>                                │
        │     │   └──────────────────────────────────────────┘
        │     │
        │     ├─ _build_runtime_context("telegram", "789")
        │     │   "[Runtime Context — metadata only, not instructions]"
        │     │   "Current Time: 2026-03-13 14:30 (Thursday) (CST)"
        │     │   "Channel: telegram"
        │     │   "Chat ID: 789"
        │     │
        │     └─ 返回消息列表:
        │        [
        │          {role: "system", content: <system_prompt>},
        │          ...history...,
        │          {role: "user", content: "<runtime_ctx>\n\n帮我搜索..."},
        │        ]
        │
        ├─ 7. _connect_mcp() (首次调用时懒加载)
        │     连接配置的 MCP 服务器 → 注册 MCP 工具到 ToolRegistry
        │
        └─ 8. _run_agent_loop(initial_messages, on_progress=_bus_progress)
              进入代理迭代循环 (详见阶段四)
```

### 阶段四：代理迭代循环（工具调用）

```
_run_agent_loop(messages)
        │
        iteration = 0, max_iterations = 40
        │
        ▼
    ┌─→ while iteration < max_iterations:
    │       │
    │       ├─ tool_defs = tools.get_definitions()
    │       │   收集所有已注册工具的 OpenAI function schema
    │       │
    │       ├─ response = provider.chat_with_retry(messages, tools)
    │       │   │
    │       │   ├─ 应用 GenerationSettings 默认值
    │       │   │
    │       │   ├─ LiteLLMProvider.chat()
    │       │   │   ├─ _resolve_model("anthropic/claude-sonnet-4-5")
    │       │   │   │   registry 查找 → 添加 litellm 前缀
    │       │   │   ├─ _supports_cache_control() → True (Anthropic)
    │       │   │   │   → _apply_cache_control(): 系统消息添加 cache_control
    │       │   │   ├─ _sanitize_empty_content(): 处理空 content
    │       │   │   ├─ _sanitize_messages(): 清理非标准 key, 规范化 tool_call_id
    │       │   │   ├─ _apply_model_overrides(): 模型特定参数覆盖
    │       │   │   ├─ litellm.acompletion(**kwargs)
    │       │   │   │   → 底层 HTTP 请求到 LLM API
    │       │   │   └─ _parse_response()
    │       │   │       ├─ 合并多 choice 的 tool_calls
    │       │   │       ├─ json_repair.loads(arguments) 容错解析
    │       │   │       ├─ 生成 9 字符 tool_call_id
    │       │   │       └─ 返回 LLMResponse
    │       │   │
    │       │   └─ 重试逻辑 (瞬态错误: 429/500/502/503/504)
    │       │       延迟 1s → 2s → 4s → 最终尝试
    │       │
    │       ├─ if response.has_tool_calls:
    │       │   │
    │       │   ├─ 发送 progress: thought 内容 (如果有)
    │       │   ├─ 发送 tool_hint: web_search("Python 异步编程...")
    │       │   │   → _bus_progress() → bus.publish_outbound(progress msg)
    │       │   │   → ChannelManager 根据 send_tool_hints 配置决定是否转发
    │       │   │
    │       │   ├─ context.add_assistant_message(messages, content, tool_calls)
    │       │   │   将助手消息(含 tool_calls)追加到 messages
    │       │   │
    │       │   ├─ 遍历每个 tool_call:
    │       │   │   │
    │       │   │   ├─ tools_used.append("web_search")
    │       │   │   ├─ logger.info("Tool call: web_search({...})")
    │       │   │   │
    │       │   │   ├─ result = tools.execute("web_search", arguments)
    │       │   │   │   │
    │       │   │   │   ├─ tool = _tools["web_search"]  (WebSearchTool)
    │       │   │   │   ├─ tool.cast_params(params)     类型转换
    │       │   │   │   ├─ tool.validate_params(params)  JSON Schema 校验
    │       │   │   │   ├─ await tool.execute(**params)  实际搜索
    │       │   │   │   │   → DuckDuckGo/Tavily/Brave/Jina/SearXNG API
    │       │   │   │   └─ 如果返回 Error → 追加 "[Analyze the error...]"
    │       │   │   │
    │       │   │   └─ context.add_tool_result(messages, tool_call_id, name, result)
    │       │   │       将工具结果追加到 messages
    │       │   │
    │       │   └─ continue → 回到循环顶部，再次调用 LLM
    │       │
    │       └─ else (无 tool_calls → 最终回复):
    │           │
    │           ├─ _strip_think(content) → 清理 <think> 块
    │           ├─ 检查 finish_reason == "error"
    │           │   ├─ 瞬态错误 (503/rate-limit/timeout 等)
    │           │   │   → 注入 user 消息 "请继续工作" → continue (保留 tool 上下文)
    │           │   └─ 永久错误 (401/invalid_key 等)
    │           │       → 不持久化到会话 (防止 error poison) → break
    │           否 → add_assistant_message(messages, clean_content)
    │           ├─ final_content = clean_content
    │           └─ break → 跳出循环
    │
    └── (循环继续直到得到最终回复或达到 max_iterations)

返回 (final_content, tools_used, all_messages)
```

### 阶段五：保存会话 + 发送回复

```
_run_agent_loop 返回后
        │
        ▼
_save_turn(session, all_msgs, skip=1+len(history))
        │
        ├─ 遍历新增的消息 (跳过已有的 system + history + user):
        │   ├─ 空 assistant 消息 (无 content 且无 tool_calls) → 跳过
        │   ├─ tool 结果超过 16KB → 截断 + "(truncated)"
        │   ├─ user 消息中的 runtime_context 前缀 → 剥离
        │   ├─ base64 图片 → 替换为 [image] 占位符
        │   └─ 添加 timestamp，追加到 session.messages
        │
        ├─ sessions.save(session)
        │   写入 workspace/sessions/telegram_789.jsonl
        │
        └─ memory_consolidator.maybe_consolidate_by_tokens(session)
           再次检查是否需要巩固

检查 MessageTool._sent_in_turn
        │
        ├─ True (代理已通过 message 工具主动发送) → 返回 None, 不再发送
        └─ False → 构造 OutboundMessage

OutboundMessage(
    channel="telegram",
    chat_id="789",
    content="以下是 Python 异步编程的最佳实践: ...",
)
        │
        ▼
bus.publish_outbound(response)
        │
        ▼
ChannelManager._dispatch_outbound()
        │
        ├─ msg = await bus.consume_outbound()
        ├─ 检查 _progress 元数据 → 非 progress，继续
        ├─ channel = channels["telegram"]  → TelegramChannel
        └─ await channel.send(msg)
              │
              ├─ Markdown → HTML 转换
              ├─ 表格 → Unicode box-drawing 渲染
              ├─ 消息分割 (Telegram 4096 字符限制)
              └─ python-telegram-bot API 发送

用户在 Telegram 收到回复 ✓
```

### 阶段六：记忆巩固（token 驱动）

```
maybe_consolidate_by_tokens(session) 被触发
        │
        ▼
async with lock (per-session 锁):
        │
        ├─ 1. estimate_session_prompt_tokens(session)
        │     构建一个 probe 消息列表 → 估算总 token 数
        │     如果 estimated < context_window_tokens → 不需要巩固, return
        │
        ├─ 2. target = context_window_tokens // 2
        │     目标是将 token 数降到窗口的一半
        │
        └─ 3. 巩固循环 (最多 5 轮):
              │
              ├─ pick_consolidation_boundary(session, tokens_to_remove)
              │   从 last_consolidated 开始扫描
              │   在 role="user" 的消息处设置边界
              │   累积 token 直到够移除的量
              │   返回 (end_idx, removed_tokens)
              │
              ├─ chunk = session.messages[last_consolidated:end_idx]
              │
              ├─ consolidate_messages(chunk)
              │   │
              │   └─ MemoryStore.consolidate(messages, provider, model)
              │       │
              │       ├─ 构建巩固 prompt:
              │       │   system: "You are a memory consolidation agent..."
              │       │   user:   当前 MEMORY.md + 需要处理的对话
              │       │
              │       ├─ provider.chat_with_retry(
              │       │     messages, tools=[save_memory],
              │       │     tool_choice=forced  ← 强制调用 save_memory
              │       │   )
              │       │
              │       ├─ 如果 tool_choice 不支持 → 降级为 auto
              │       │
              │       ├─ 提取 save_memory 工具调用的参数:
              │       │   history_entry: "[2026-03-13 14:30] 用户询问了..."
              │       │   memory_update: "# 关于用户\n- 对 Python 感兴趣\n..."
              │       │
              │       ├─ append_history(history_entry) → HISTORY.md
              │       ├─ write_long_term(memory_update) → MEMORY.md
              │       │
              │       └─ 失败降级:
              │           连续失败 < 3 次 → return False (暂不处理)
              │           连续失败 >= 3 次 → _raw_archive()
              │             将原始消息直接 dump 到 HISTORY.md
              │             "[RAW] 15 messages\n[14:30] USER: ...\n..."
              │
              ├─ session.last_consolidated = end_idx
              │   指针前移, 会话消息不删除 (append-only)
              │
              ├─ sessions.save(session)
              │
              └─ 重新估算 token → 如果还超标 → 继续下一轮
```

### 阶段七：进程关闭

```
收到 SIGINT (Ctrl+C) 或 SIGTERM
        │
        ▼
gateway() 的 run() finally 块:
        │
        ├─ agent.close_mcp()     → 关闭 MCP 连接
        ├─ heartbeat.stop()      → 停止心跳循环
        ├─ cron.stop()           → 停止定时任务
        ├─ agent.stop()          → 设置 _running = False
        └─ channels.stop_all()   → 逐个停止渠道
              ├─ 取消 outbound dispatcher task
              └─ 各渠道 stop() (关闭连接、清理资源)
```

### 生命周期总结图

```
        ┌──────────────── 进程启动 ────────────────┐
        │                                          │
        │  Config → Provider → Bus → Agent → Channels
        │                                          │
        └──────────────────────────────────────────┘
                          │
                          ▼
        ┌──────────── 稳态运行 ──────────────────┐
        │                                        │
        │  Channel.start() ─→ bus.inbound ─→ AgentLoop.run()
        │       ↑                                   │
        │       │                                   ▼
        │  Channel.send() ←─ bus.outbound ←─ _process_message()
        │                                   │       │
        │                                   │    context.build_messages()
        │                                   │       │
        │                                   │    provider.chat_with_retry()
        │                                   │       │
        │                                   │    tools.execute() ←──┐
        │                                   │       │               │
        │                                   │       └───────────────┘
        │                                   │       (循环直到得到最终回复)
        │                                   │
        │                                   ├── _save_turn() → session JSONL
        │                                   └── maybe_consolidate()
        │                                        → MEMORY.md + HISTORY.md
        │                                        │
        └────────────────────────────────────────┘
                          │
                          ▼
        ┌──────────── 进程关闭 ──────────────────┐
        │                                        │
        │  close_mcp → stop heartbeat/cron/agent → stop channels
        │                                        │
        └────────────────────────────────────────┘
```

---

## 五、核心模块详解

### 5.1 AgentLoop（`agent/loop.py`）

系统的"心脏"，职责：接收消息 → 构建上下文 → 调用 LLM → 执行工具 → 返回回复。

**关键属性**：

| 属性                  | 类型                 | 说明                       |
| --------------------- | -------------------- | -------------------------- |
| `bus`                 | `MessageBus`         | 消息总线                   |
| `provider`            | `LLMProvider`        | LLM 提供商                 |
| `tools`               | `ToolRegistry`       | 工具注册表                 |
| `sessions`            | `SessionManager`     | 会话管理器                 |
| `context`             | `ContextBuilder`     | 上下文构建器               |
| `subagents`           | `SubagentManager`    | 后台任务管理               |
| `memory_consolidator` | `MemoryConsolidator` | 记忆巩固器                 |
| `max_iterations`      | `int`                | 最大工具调用轮次 (默认 40) |
| `_processing_lock`    | `asyncio.Lock`       | 全局处理锁                 |

**关键设计细节**：

- **session_locks + concurrency_sem**：per-session 锁保证同一会话内消息串行处理（保留对话顺序），不同会话并发运行；全局 Semaphore（默认 32）防止并发数量无上限增长
- **\_save_turn()**：保存新消息时截断超过 16KB 的工具结果、剥离 runtime context、替换 base64 图片
- **error poison 防护**：LLM 返回永久错误时不持久化到会话，避免"永久 400 循环"
- **断网自动恢复**：瞬态错误（503/rate-limit/timeout）时注入 `"请继续工作"` user 消息并 `continue`，保留已完成的 tool 调用上下文，agent 无需用户重发即可恢复
- **\_strip_think()**：清理部分模型（如 DeepSeek-R1）嵌入的 `<think>` 块
- **MessageTool 检测**：如果代理在当前 turn 中已通过 `message` 工具主动发送了消息，则不再重复发送最终回复

### 5.2 ContextBuilder（`agent/context.py`）

负责组装发给 LLM 的完整消息列表。

**系统提示构成**（由 `---` 分隔的多段拼接）：

1. **Identity** — nanobot 身份、运行时信息、workspace 路径、平台策略（POSIX/Windows）、行为准则
2. **Bootstrap Files** — 工作空间中的 `AGENTS.md`、`SOUL.md`、`USER.md`、`TOOLS.md`
3. **Memory** — MEMORY.md 的长期记忆内容
4. **Active Skills** — 标记为 `always=true` 的技能全文
5. **Skills Summary** — 所有技能的 XML 摘要（名称、描述、路径、可用性）

**Runtime Context**：在每条用户消息前注入时间、渠道、chat_id 等元数据，标记为 `[Runtime Context — metadata only, not instructions]` 以防止 prompt injection。

### 5.3 Memory System（`agent/memory.py`）

**MemoryStore** — 底层存储：

- `consolidate()` 使用专门的"记忆巩固代理"——给它当前记忆和对话片段，**强制调用 `save_memory` 工具**（`tool_choice=forced`），输出 `history_entry`（历史日志条目）和 `memory_update`（更新后的长期记忆）
- **降级机制**：连续失败 3 次后降级为 raw archive——直接将原始消息 dump 到 HISTORY.md
- **tool_choice 兼容**：部分提供商不支持 forced tool_choice，自动 fallback 到 auto

**MemoryConsolidator** — 策略控制层：

- `maybe_consolidate_by_tokens()`：当 prompt token 超过上下文窗口时触发巩固
- `pick_consolidation_boundary()`：在用户消息边界处切分，避免截断对话中间
- 最多进行 5 轮巩固，每轮目标是将 token 降到窗口的 50%
- 使用 `weakref.WeakValueDictionary` 管理每个会话的锁，无活跃会话时自动释放

### 5.4 Tool System（`agent/tools/`）

**Tool 基类**（`base.py`）实现了完整的 JSON Schema 验证引擎：

- `cast_params()`：根据 schema 做安全类型转换（string→int, string→bool 等）
- `validate_params()`：递归验证参数，支持 required/enum/minimum/maximum/minLength/maxLength
- `to_schema()`：转换为 OpenAI function calling 格式

**ToolRegistry**（`registry.py`）极其简洁（~70 行）：

- `execute()` 在工具返回 Error 时自动追加 `[Analyze the error above and try a different approach.]` 提示，引导 LLM 换策略而非重复犯错

**内置工具一览**：

| 工具         | 功能                           | 安全措施                                  |
| ------------ | ------------------------------ | ----------------------------------------- |
| `read_file`  | 读取文件（分页，默认 2000 行） | 可选 workspace 限制                       |
| `write_file` | 创建/覆写文件                  | 可选 workspace 限制                       |
| `edit_file`  | 精确字符串替换编辑             | diff 预览                                 |
| `list_dir`   | 列出目录                       | —                                         |
| `exec`       | 执行 shell 命令                | 60s 超时 + 危险命令黑名单                 |
| `web_search` | 网页搜索                       | 支持 DuckDuckGo/Tavily/Brave/Jina/SearXNG |
| `web_fetch`  | 获取网页内容                   | URL 验证                                  |
| `message`    | 发送消息到聊天渠道             | —                                         |
| `spawn`      | 启动后台 subagent              | 会话级任务管理                            |
| `cron`       | 创建定时任务                   | —                                         |

### 5.5 Provider System（`providers/`）

**LLMProvider 基类**（`base.py`）：

- `chat()`：抽象方法，由子类实现
- `chat_with_retry()`：自动重试瞬态错误（429/500/502/503/504/timeout），延迟 1→2→4→8→16→30 秒（共 6 次重试，总窗口 61 秒）
- `GenerationSettings`：frozen dataclass，存储默认的 temperature/max_tokens/reasoning_effort
- `_sanitize_empty_content()`：处理空内容（MCP 工具返回空值时），避免提供商 400 错误

**LiteLLMProvider**（`litellm_provider.py`）：

- `_resolve_model()`：根据 registry 自动添加 litellm 前缀（如 `claude-sonnet-4-5` → `anthropic/claude-sonnet-4-5`）
- `_apply_cache_control()`：为支持 prompt caching 的提供商（如 Anthropic）注入 `cache_control` 标记
- `_sanitize_messages()`：清理非标准 key、规范化 tool_call_id（9 字符 SHA1，兼容 Mistral 等严格提供商）
- `_parse_response()`：合并多 choice 的 tool_calls（GitHub Copilot 等拆分响应的提供商）
- 使用 `json_repair.loads()` 解析 tool arguments，容错处理格式错误的 JSON

**其他提供商**：

- `CustomProvider`：直接调用 OpenAI 兼容端点（Ollama, vLLM 等），绕过 LiteLLM
- `AzureOpenAIProvider`：Azure OpenAI 直接 API，deployment 名称作为模型标识
- `OpenAICodexProvider`：OAuth 2.0 认证流程

### 5.6 Session Management（`session/manager.py`）

**Session**：

- 消息列表是 **append-only** 的，巩固不会删除消息，只移动 `last_consolidated` 指针
- `get_history()` 只返回未巩固的消息，且从最近的 user 消息开始对齐，避免孤立的 tool_result
- 这种设计对 LLM prompt cache 友好——历史消息不变意味着 cache 命中率高

**SessionManager**：

- JSONL 格式持久化（第一行是 metadata，后续每行一条消息）
- 内存缓存 + 磁盘持久化
- 支持从旧版全局目录 `~/.nanobot/sessions/` 自动迁移

### 5.7 Channel System（`channels/`）

**BaseChannel** 定义了三个核心抽象：`start()`、`stop()`、`send()`

**ChannelManager**：

- 使用 `pkgutil` 自动发现渠道模块（`channels/registry.py`）
- 启动时验证 `allow_from` 不为空（空列表=拒绝所有，这是安全设计——必须显式配置）
- outbound dispatcher 循环消费 outbound 队列，按 `msg.channel` 分发
- 支持 `send_progress` 和 `send_tool_hints` 配置，控制是否转发中间状态

**已实现的渠道**：Telegram、Discord、WhatsApp（通过 Node.js Bridge）、飞书、钉钉、Email（IMAP/SMTP）、Matrix、Slack

### 5.8 Skills System（`agent/skills.py`）

- 两级来源：workspace 自定义技能 > 内置技能
- Frontmatter 中可声明 `requires`（bins/env），加载时自动检查可用性
- `build_skills_summary()` 生成 XML 格式摘要，包含 `available` 属性
- `always=true` 的技能全文注入系统提示，其他技能按需加载

### 5.9 MessageBus（`bus/queue.py`）

极简设计（~45 行），两个 `asyncio.Queue`：

- `inbound`：Channel → Agent 方向
- `outbound`：Agent → Channel 方向
- 完全异步，无锁（`asyncio.Queue` 本身是协程安全的）

### 5.10 CLI 系统（`cli/commands.py`）

**两种运行模式**：

1. **Gateway 模式**（`nanobot gateway`）：启动所有渠道 + 代理 + cron + heartbeat，长驻运行
2. **Agent 模式**（`nanobot agent`）：
   - 单消息模式（`-m "message"`）：`process_direct()` 直接调用，不走 bus
   - 交互模式（无 `-m`）：`prompt_toolkit` 提供历史、粘贴、编辑功能

**终端兼容性处理**：

- Windows UTF-8 强制编码
- `_flush_pending_tty_input()`：丢弃模型生成期间的按键输入
- `_restore_terminal()`：通过 termios 恢复终端状态
- 信号处理：SIGINT/SIGTERM/SIGHUP 优雅退出，SIGPIPE 忽略

---

## 六、数据流总览

```
┌─────────────┐     InboundMessage      ┌─────────────┐
│  Telegram    │──────────────────────→  │             │
│  Discord     │    asyncio.Queue        │  MessageBus │
│  WhatsApp    │←──────────────────────  │             │
│  飞书/钉钉   │     OutboundMessage     └──────┬──────┘
│  Email/Slack │                                │
└─────────────┘                                │
                                               ▼
                                      ┌────────────────┐
                                      │   AgentLoop    │
                                      │                │
                                      │  ContextBuilder│──→ system prompt + history
                                      │  SessionManager│──→ 会话持久化 (JSONL)
                                      │  MemoryStore   │──→ MEMORY.md + HISTORY.md
                                      │  ToolRegistry  │──→ 工具执行
                                      │  SubagentMgr   │──→ 后台任务
                                      └───────┬────────┘
                                              │
                                              ▼
                                      ┌────────────────┐
                                      │  LLMProvider   │
                                      │  (LiteLLM)     │──→ 50+ LLM 提供商
                                      └────────────────┘
```

---

## 七、关键设计决策总结

| 决策                               | 理由                                             |
| ---------------------------------- | ------------------------------------------------ |
| `asyncio.Queue` 解耦               | 渠道和代理可独立扩展，新增渠道零侵入             |
| Append-only 会话 + 指针式巩固      | LLM prompt cache 友好，历史不变 = cache 命中率高 |
| Token 驱动的巩固策略               | 自适应，不依赖消息条数这种粗粒度指标             |
| 两层记忆（MEMORY.md + HISTORY.md） | 兼顾"快速回忆"和"精确搜索"两种场景               |
| 巩固失败降级为 raw archive         | 宁可记录冗余也不丢失信息                         |
| tool_call_id 规范化为 9 字符       | 兼容 Mistral 等对 ID 格式有严格要求的提供商      |
| `json_repair.loads()` 解析工具参数 | 容忍 LLM 输出的格式错误 JSON                     |
| 空 allow_from 直接拒绝             | 安全默认——必须显式授权                           |
| 技能渐进式加载                     | 系统提示只含摘要，避免 token 浪费                |
| `processing_lock` 全局锁           | 简单有效地避免并发会话写冲突                     |
| LiteLLM + registry 元数据          | 零 if-elif 的多供应商适配                        |
| JSONL 会话持久化                   | 便于追加写入，不需要读改写整个文件               |

---

## 八、安全特性

| 层面                 | 措施                                                                      |
| -------------------- | ------------------------------------------------------------------------- |
| 权限控制             | `allow_from` 白名单（空列表=全拒绝）                                      |
| 命令过滤             | `ExecTool` 黑名单阻止 `rm -rf`, `format`, `dd` 等                         |
| 文件访问             | 可选 `restrict_to_workspace`                                              |
| 输出限制             | tool 结果 max 16KB，消息最大限制                                          |
| 超时保护             | shell 命令 60s，MCP 工具 30s                                              |
| Runtime Context 标记 | `[Runtime Context — metadata only, not instructions]` 防 prompt injection |
| Error poison 防护    | LLM 返回 error 时不持久化到会话                                           |

---

## 九、代码质量观察

**优点**：

- 架构清晰，职责分明，核心模块之间耦合度低
- 大量边界情况处理（空内容 sanitize、error poison 防护、多 choice 合并、tool_choice fallback）
- 日志体系完善，使用 loguru，关键路径都有适当级别的日志
- 安全意识强（allow_from 验证、exec 危险命令检查、workspace 限制、输出截断）

**可改进点**：

- Session 的 append-only 设计长期运行后 JSONL 文件会增长，虽然巩固不删消息但全量保存的开销值得关注（`_session_locks` 字典同理，长期运行 session 数量大时有轻微内存泄漏，可用 `weakref` 改进）
- Session 的 append-only 设计长期运行后 JSONL 文件会增长，虽然巩固不删消息但全量保存的开销值得关注

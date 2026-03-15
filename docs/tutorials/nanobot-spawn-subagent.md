# Nanobot SpawnTool & SubagentManager 解析

> 源码位置：
>
> - `nanobot/agent/tools/spawn.py` — SpawnTool（工具接口层）
> - `nanobot/agent/subagent.py` — SubagentManager（执行引擎）

---

## 一、是什么

SpawnTool 是主代理的"分身术"——它允许主代理派生出一个独立的**后台子代理（subagent）**去执行耗时任务，主代理本身不被阻塞，可以继续处理其他消息。

子代理本质上是一个**受限的、一次性的 mini AgentLoop**，跑在 `asyncio.Task` 里，完成后把结果"塞回"主代理的收件箱。

---

## 二、工作流程

```
主代理处理用户消息
  │
  ├─ 判断任务耗时/可独立执行
  │
  ├─ 调用 spawn(task="帮我分析这个日志文件并写报告", label="日志分析")
  │     │
  │     ├─ 生成 task_id (8 位 UUID)
  │     ├─ asyncio.create_task() → 后台运行，不阻塞
  │     └─ 立即返回 "Subagent [日志分析] started (id: a1b2c3d4)"
  │
  ├─ 主代理回复用户："已在后台启动日志分析任务"
  │
  └─ 继续处理下一条消息（不等待子代理完成）

与此同时，子代理在后台独立运行：
  │
  ├─ 拥有自己的 ToolRegistry（7 个工具，无 message/spawn/cron）
  ├─ 拥有精简的 system prompt（专注任务，不含主代理的完整上下文）
  ├─ 独立的 agent loop（最多 15 轮迭代，比主代理的 40 轮少）
  ├─ 调用 LLM → 执行工具 → 循环直到完成
  │
  └─ 完成后 → _announce_result()
        │
        ├─ 构造 InboundMessage(channel="system", sender_id="subagent")
        │   内容: "[Subagent '日志分析' completed successfully]
        │          Task: ...
        │          Result: ..."
        │   附加指令: "Summarize this naturally for the user."
        │
        └─ bus.publish_inbound(msg) → 推回主代理的 inbound 队列
              │
              主代理收到后作为 system 消息处理
              → 调用 LLM 生成自然语言摘要
              → 发送给用户: "日志分析完成，发现 3 个关键错误..."
```

---

## 三、关键设计决策

| 特性           | 说明                                                                              |
| -------------- | --------------------------------------------------------------------------------- |
| **工具阉割**   | 子代理没有 `message`/`spawn`/`cron`——不能发消息、不能再套娃派生、不能创建定时任务 |
| **迭代上限**   | 15 轮 vs 主代理 40 轮，防止后台任务失控                                           |
| **结果回注**   | 通过 `channel="system"` 的 InboundMessage 回注主代理，走正常的消息处理流程        |
| **会话级管理** | `_session_tasks` 按 session_key 追踪，`/stop` 可取消该会话所有子代理              |
| **自动清理**   | task 完成后通过 `done_callback` 自动从 `_running_tasks` 移除                      |

---

## 四、代码实现详解

### 4.1 SpawnTool — 工具接口层

`spawn.py` 是一个薄壳，仅负责将 LLM 的工具调用转发给 `SubagentManager`。

```python
# spawn.py

class SpawnTool(Tool):
    """Tool to spawn a subagent for background task execution."""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"
        self._session_key = "cli:direct"
```

**构造时**接收 `SubagentManager` 引用。`_origin_channel` / `_origin_chat_id` 记录当前消息的来源渠道，用于子代理完成后把结果送回正确的聊天。

```python
    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the origin context for subagent announcements."""
        self._origin_channel = channel
        self._origin_chat_id = chat_id
        self._session_key = f"{channel}:{chat_id}"
```

每次主代理处理新消息时，`AgentLoop._set_tool_context()` 会调用此方法更新渠道上下文。这确保了子代理完成后，结果能发回**正确的聊天窗口**，而不是上一条消息的来源。

```python
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the subagent to complete",
                },
                "label": {
                    "type": "string",
                    "description": "Optional short label for the task (for display)",
                },
            },
            "required": ["task"],
        }
```

暴露给 LLM 的参数 schema：`task`（必填，任务描述）和 `label`（可选，简短标签用于显示）。

```python
    async def execute(self, task: str, label: str | None = None, **kwargs: Any) -> str:
        return await self._manager.spawn(
            task=task,
            label=label,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
            session_key=self._session_key,
        )
```

直接委托给 `SubagentManager.spawn()`，传入任务内容和来源上下文。

---

### 4.2 SubagentManager — 执行引擎

#### 4.2.1 初始化

```python
# subagent.py

class SubagentManager:
    def __init__(self, provider, workspace, bus, model=None, ...):
        self.provider = provider          # LLM 提供商（与主代理共享）
        self.workspace = workspace        # 工作空间路径
        self.bus = bus                    # 消息总线（用于回注结果）
        self.model = model or provider.get_default_model()
        self._running_tasks: dict[str, asyncio.Task[None]] = {}     # task_id → Task
        self._session_tasks: dict[str, set[str]] = {}               # session_key → {task_ids}
```

两个追踪字典：

- `_running_tasks`：所有正在运行的子代理，按 task_id 索引
- `_session_tasks`：按会话分组的 task_id 集合，支持 `/stop` 批量取消

#### 4.2.2 派生子代理

```python
    async def spawn(self, task, label=None, origin_channel="cli",
                    origin_chat_id="direct", session_key=None) -> str:
        task_id = str(uuid.uuid4())[:8]                              # (1) 8 位短 ID
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")

        bg_task = asyncio.create_task(                               # (2) 后台 task
            self._run_subagent(task_id, task, display_label, origin)
        )
        self._running_tasks[task_id] = bg_task                       # (3) 注册追踪
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:                       # (4) 完成回调
            self._running_tasks.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)

        return f"Subagent [{display_label}] started (id: {task_id})."
```

关键步骤：

1. 生成 8 位 UUID 短 ID，便于日志和用户显示
2. `asyncio.create_task()` 创建后台协程，**立即返回不等待**
3. 注册到两个追踪字典
4. `done_callback` 确保任务完成/取消/失败后自动清理引用

**注意**：`spawn()` 是同步返回的——它告诉主代理"已启动"，主代理可以立刻继续工作。

#### 4.2.3 子代理执行循环

```python
    async def _run_subagent(self, task_id, task, label, origin) -> None:
        try:
            # --- 构建受限工具集 ---
            tools = ToolRegistry()
            tools.register(ReadFileTool(...))
            tools.register(WriteFileTool(...))
            tools.register(EditFileTool(...))
            tools.register(ListDirTool(...))
            tools.register(ExecTool(...))
            tools.register(WebSearchTool(...))
            tools.register(WebFetchTool(...))
            # 注意：没有 MessageTool, SpawnTool, CronTool
```

子代理的工具集是主代理的子集（7 个 vs 10 个）。三个被移除的工具：

- **MessageTool**：子代理不应直接给用户发消息（结果由主代理转述）
- **SpawnTool**：防止递归派生（套娃）
- **CronTool**：后台任务不应创建定时任务

```python
            # --- 构建精简的系统提示 ---
            system_prompt = self._build_subagent_prompt()
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]
```

子代理的系统提示非常精简——只包含身份声明、workspace 路径和技能摘要，不含主代理的记忆和完整上下文。

```python
            # --- 迭代循环 ---
            max_iterations = 15
            iteration = 0
            final_result = None

            while iteration < max_iterations:
                iteration += 1
                response = await self.provider.chat_with_retry(
                    messages=messages,
                    tools=tools.get_definitions(),
                    model=self.model,
                )

                if response.has_tool_calls:
                    # 追加 assistant 消息 + 执行工具 + 追加 tool 结果
                    messages.append(build_assistant_message(...))
                    for tool_call in response.tool_calls:
                        result = await tools.execute(tool_call.name, tool_call.arguments)
                        messages.append({"role": "tool", ...})
                else:
                    final_result = response.content
                    break
```

这是一个简化版的 AgentLoop：

- 没有记忆巩固
- 没有会话持久化
- 没有 progress 回调
- 没有斜杠命令处理
- 最多 15 轮（主代理是 40 轮）

```python
            # --- 宣布结果 ---
            await self._announce_result(task_id, label, task, final_result, origin, "ok")

        except Exception as e:
            await self._announce_result(task_id, label, task, f"Error: {e}", origin, "error")
```

无论成功还是失败，都通过 `_announce_result` 回报。

#### 4.2.4 结果回注机制

```python
    async def _announce_result(self, task_id, label, task, result, origin, status):
        status_text = "completed successfully" if status == "ok" else "failed"

        announce_content = f"""[Subagent '{label}' {status_text}]

Task: {task}

Result:
{result}

Summarize this naturally for the user. Keep it brief (1-2 sentences).
Do not mention technical details like "subagent" or task IDs."""

        msg = InboundMessage(
            channel="system",            # 标记为系统消息
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",   # 编码原始来源
            content=announce_content,
        )

        await self.bus.publish_inbound(msg)
```

这是最巧妙的设计——子代理不直接给用户发消息，而是：

1. 构造一条 `channel="system"` 的 `InboundMessage`
2. `chat_id` 编码为 `"telegram:789"` 格式（原始渠道:聊天 ID）
3. 推入 `bus.inbound` 队列

主代理的 `_process_message()` 收到后（`loop.py:346-364`）：

```python
# AgentLoop._process_message() 中的 system 消息处理:
if msg.channel == "system":
    channel, chat_id = msg.chat_id.split(":", 1)   # 解码出 "telegram", "789"
    # ... 用 LLM 生成自然语言摘要 ...
    return OutboundMessage(channel=channel, chat_id=chat_id, content=...)
```

主代理会调用 LLM 将子代理的结果转述为自然语言，然后发送到用户的聊天窗口。用户看到的是一条流畅的回复，而不是技术细节。

#### 4.2.5 子代理的身份与技能——从哪里来？

子代理**不是预配置的独立 agent**，而是运行时动态创建的临时 mini agent。它没有独立的身份设定文件，也不读取任何 bootstrap 文件和记忆。

**身份来源**：硬编码在 `_build_subagent_prompt()` 中的几行文字。

```python
    def _build_subagent_prompt(self) -> str:
        time_ctx = ContextBuilder._build_runtime_context(None, None)
        parts = [f"""# Subagent

{time_ctx}

You are a subagent spawned by the main agent to complete a specific task.
Stay focused on the assigned task. Your final response will be reported
back to the main agent.

## Workspace
{self.workspace}"""]

        skills_summary = SkillsLoader(self.workspace).build_skills_summary()
        if skills_summary:
            parts.append(f"## Skills\n\n...")

        return "\n\n".join(parts)
```

**技能来源**：与主代理共享同一个 `workspace/skills/` 目录。`SkillsLoader(self.workspace)` 读取的是同一套技能文件，但只注入摘要索引，不注入全文。子代理如果需要使用某个技能，需要自己用 `read_file` 工具去读取对应的 `SKILL.md`。

**主代理 vs 子代理的系统提示构建路径对比**：

```
主代理 ContextBuilder.build_system_prompt():
  ① _get_identity()              → 身份 + 运行时信息 + 平台策略 + 行为准则
  ② _load_bootstrap_files()      → AGENTS.md / SOUL.md / USER.md / TOOLS.md
  ③ memory.get_memory_context()  → MEMORY.md 长期记忆
  ④ skills.load_skills_for_context() → always=true 技能全文
  ⑤ skills.build_skills_summary()   → 所有技能 XML 摘要

子代理 SubagentManager._build_subagent_prompt():
  ① 硬编码文字                   → "You are a subagent..."（3 行）
  ② skills.build_skills_summary() → 所有技能 XML 摘要（仅此而已）
```

**逐项对比**：

| 系统提示内容         | 主代理                                                                                           | 子代理                                                          |
| -------------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------------------------- |
| 身份声明             | `_get_identity()` — "You are nanobot, a helpful AI assistant" + 运行时信息 + 平台策略 + 行为准则 | 硬编码 — "You are a subagent spawned by the main agent"（3 行） |
| AGENTS.md            | ✅ 加载                                                                                          | ❌ 不加载                                                       |
| SOUL.md              | ✅ 加载                                                                                          | ❌ 不加载                                                       |
| USER.md              | ✅ 加载                                                                                          | ❌ 不加载                                                       |
| TOOLS.md             | ✅ 加载                                                                                          | ❌ 不加载                                                       |
| MEMORY.md 长期记忆   | ✅ 注入系统提示                                                                                  | ❌ 不读取                                                       |
| always=true 技能全文 | ✅ 注入                                                                                          | ❌ 不注入                                                       |
| 技能摘要 (XML 索引)  | ✅ 有                                                                                            | ✅ 有（同一 workspace）                                         |
| Runtime Context      | ✅ 时间 + 渠道 + chat_id                                                                         | ⚠️ 只有时间（渠道和 chat_id 传 None）                           |

**总结**：子代理是一个"**无记忆、无人设、工具受限**"的一次性执行器。它唯一知道的就是"我是被主代理派生的，完成任务后汇报结果"。如果需要给子代理更丰富的上下文，当前设计下只能通过 `spawn(task=...)` 的 task 参数传入——主代理在调用 spawn 时把必要的背景信息写进 task 描述里。

#### 4.2.6 会话级取消

```python
    async def cancel_by_session(self, session_key: str) -> int:
        tasks = [
            self._running_tasks[tid]
            for tid in self._session_tasks.get(session_key, [])
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        ]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)
```

当用户发送 `/stop` 时，`AgentLoop._handle_stop()` 调用此方法：

1. 查找该会话下所有未完成的子代理 task
2. 逐个 `cancel()`
3. `gather` 等待取消完成
4. 返回取消的数量（合并到 `/stop` 的回复中："Stopped 3 task(s)."）

---

## 五、主代理与子代理对比

| 维度         | 主代理 (AgentLoop)              | 子代理 (SubagentManager)        |
| ------------ | ------------------------------- | ------------------------------- |
| 工具数量     | 10 个                           | 7 个（无 message/spawn/cron）   |
| 迭代上限     | 40 轮                           | 15 轮                           |
| 系统提示     | 完整（身份+记忆+技能+引导文件） | 精简（身份+workspace+技能摘要） |
| 会话持久化   | JSONL 文件                      | 无（内存中运行，完成即丢弃）    |
| 记忆巩固     | 有（token 驱动）                | 无                              |
| 运行方式     | 长驻循环                        | 一次性 `asyncio.Task`           |
| 与用户通信   | 直接发送 OutboundMessage        | 间接：结果回注 → 主代理转述     |
| 可派生子代理 | 可以                            | 不可以                          |

---

## 六、典型使用场景

- 耗时的文件分析/代码审查
- 后台执行长时间 shell 命令
- 并行处理多个独立子任务
- 网页抓取和信息汇总
- 任何不需要用户实时交互的独立任务

---

## 七、设计权衡

**优势**：

- 主代理不被长任务阻塞，保持响应性
- 工具阉割 + 迭代上限 = 防止失控
- 结果回注主代理，由 LLM 转述 = 用户体验一致

**局限**：

- 子代理没有会话历史/记忆，无法理解之前的对话上下文
- 全局 `_processing_lock` 意味着结果回注时也需要排队等待主代理空闲
- 无法嵌套派生（设计如此，防止无限递归）
- 子代理的中间进度对用户不可见（只有最终结果）

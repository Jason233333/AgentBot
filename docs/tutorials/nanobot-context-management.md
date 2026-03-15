# Nanobot 上下文管理机制深度解析

> 源码位置：
>
> - `nanobot/agent/context.py` — ContextBuilder（上下文组装）
> - `nanobot/agent/memory.py` — MemoryStore + MemoryConsolidator（记忆存储与巩固）
> - `nanobot/session/manager.py` — Session + SessionManager（会话管理）
> - `nanobot/agent/loop.py` — AgentLoop.\_save_turn()（数据清洗与持久化）
> - `nanobot/utils/helpers.py` — Token 估算工具函数
> - `nanobot/skills/memory/SKILL.md` — 记忆技能（always=true）
> - `nanobot/templates/` — 工作空间模板文件

---

## FAQ：常见问题速查

### Q1：consolidate 之后 session 的 message_list 会变吗？

**不变。** message_list 是 append-only 的，消息永远不删除。consolidate 只移动 `last_consolidated` 指针，让 `get_history()` 跳过已巩固的消息。

```
consolidate 前：
messages = [m0, m1, m2, m3, m4, m5, m6, m7, m8]
             ↑ last_consolidated=0
get_history() → [m0, m1, m2, m3, m4, m5, m6, m7, m8]  (全部)

consolidate 后（假设巩固了 m0~m3）：
messages = [m0, m1, m2, m3, m4, m5, m6, m7, m8]   ← 一模一样，没有任何删除
                             ↑ last_consolidated=4
get_history() → [m4, m5, m6, m7, m8]               ← 只是跳过了前 4 条
```

巩固的"产物"写到了 MEMORY.md 和 HISTORY.md，**不会回写到 message_list 里**。没有"把摘要设置为第一条"这种操作。

### Q2：如果需要多次巩固，是扩大窗口还是把摘要和后续消息一起做新的摘要？

**都不是。** 是逐段前进，每段独立巩固（最多 5 轮）：

```
第 1 轮：巩固 messages[0:4]  → last_consolidated = 4  → LLM 总结这段 → 写入文件
第 2 轮：巩固 messages[4:8]  → last_consolidated = 8  → LLM 总结这段 → 写入文件
第 3 轮：巩固 messages[8:12] → last_consolidated = 12 → LLM 总结这段 → 写入文件
...每轮独立调 LLM，互不依赖
```

每轮的 LLM 输入是：那段 chunk 的原始消息 + **当前** MEMORY.md。前一轮更新了 MEMORY.md 后，后一轮会读到更新后的版本，但 HISTORY.md 的条目各自独立。不存在"把上一轮的摘要和后续消息混在一起再做摘要"的嵌套结构。

### Q3：Session 什么时候创建新的？会一直增长吗？

**一个 `channel:chat_id` 对应一个 Session。** 例如 `telegram:123456`、`cli:direct`。

**是的，它会一直增长。** 即使做了 consolidate，messages 不会被删除（append-only），JSONL 文件只增不减。唯一清空的方式是 `/new` 命令：

```python
if cmd == "/new":
    # 1. 先把未巩固的全部巩固到 MEMORY.md + HISTORY.md
    await self.memory_consolidator.archive_unconsolidated(session)
    # 2. 然后清空
    session.clear()     # messages = [], last_consolidated = 0
```

这是 append-only 设计为 prompt cache 优化做出的牺牲——保持历史消息不变以最大化 cache 命中率，代价是磁盘空间。但对运行时性能影响不大：`get_history()` 只返回 `messages[last_consolidated:]`，全量消息只在启动时加载一次。

### Q4：如果 MEMORY.md 非常长怎么办？

**目前没有任何机制处理这个问题。** 这是代码中的一个已知局限。

```python
# context.py:35-37 — 无论多大，原样注入
memory = self.memory.get_memory_context()
if memory:
    parts.append(f"# Memory\n\n{memory}")
```

没有截断、没有摘要、没有分页。如果 MEMORY.md 膨胀到几千 token，会：

1. **挤压可用的 history 空间** — system prompt 变大 → 留给 history 的 token 变少 → 巩固更频繁
2. **最终可能导致单条 system prompt 就超过 context window** — 系统会进入"巩固了也没用"的死循环

实际上有两个"软制约"：

- 巩固代理的 prompt 要求 `"Include all existing facts plus new ones. Return unchanged if nothing new."`——LLM 倾向于保持 MEMORY.md 紧凑
- 代理自身可以用 `edit_file` 主动精简 MEMORY.md

但这些都依赖"LLM 自律"，没有硬性保证。详见 [优化建议](../can_be_optimize/nanobot-memory-unbounded-growth.md)。

---

## 一、概览：上下文的三层架构

Nanobot 的上下文管理分为三层，每层有不同的生命周期和存储介质：

```
┌─────────────────────────────────────────────────────────────────┐
│                    发送给 LLM 的完整 prompt                      │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  System Prompt（每次请求重建）                            │   │
│  │  ├─ Identity（硬编码）                                   │   │
│  │  ├─ Bootstrap Files（AGENTS.md / SOUL.md / USER.md ...） │   │
│  │  ├─ 长期记忆（MEMORY.md）          ← 持久层              │   │
│  │  ├─ Active Skills（always=true）                         │   │
│  │  └─ Skills Summary（XML 索引）                           │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  History Messages（会话中未巩固的消息）← 短期层           │   │
│  │  user → assistant → tool → assistant → ...               │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Current User Message + Runtime Context   ← 瞬时层       │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘

               ┌─────────────────────────────┐
               │  HISTORY.md（归档层）        │
               │  不加载到 prompt              │
               │  grep 可搜索                  │
               └─────────────────────────────┘
```

| 层次       | 存储                                  | 加载方式                     | 生命周期                   |
| ---------- | ------------------------------------- | ---------------------------- | -------------------------- |
| **持久层** | MEMORY.md（Markdown 文件）            | 每次请求注入 system prompt   | 跨会话，永久存在           |
| **短期层** | Session.messages（内存 + JSONL 文件） | 作为 history 消息列表        | 单次会话内，巩固后不再加载 |
| **瞬时层** | 当前 turn 的消息（内存）              | 代理迭代循环中追加           | 单次 turn，持久化后丢弃    |
| **归档层** | HISTORY.md（追加写入文件）            | 不加载到 prompt，需主动 grep | 永久存在，只增不减         |

---

## 二、持久层：长期记忆（MEMORY.md）

### 2.1 文件位置与模板

```
workspace/
└── memory/
    ├── MEMORY.md    ← 长期记忆（注入 system prompt）
    └── HISTORY.md   ← 历史日志（grep 搜索用）
```

初始模板（`templates/memory/MEMORY.md`）：

```markdown
# Long-term Memory

This file stores important information that should persist across sessions.

## User Information

(Important facts about the user)

## Preferences

(User preferences learned over time)

## Project Context

(Information about ongoing projects)

## Important Notes

(Things to remember)
```

### 2.2 读取路径

每次 LLM 调用前，`ContextBuilder.build_system_prompt()` 都会**实时读取** MEMORY.md：

```python
# context.py:35-37
memory = self.memory.get_memory_context()   # → MemoryStore.read_long_term()
if memory:
    parts.append(f"# Memory\n\n{memory}")
```

```python
# memory.py:86-89 — MemoryStore
def read_long_term(self) -> str:
    if self.memory_file.exists():
        return self.memory_file.read_text(encoding="utf-8")
    return ""

def get_memory_context(self) -> str:
    long_term = self.read_long_term()
    return f"## Long-term Memory\n{long_term}" if long_term else ""
```

这意味着：

- MEMORY.md 的内容**每次请求都重新读取**，而非缓存
- 代理用 `edit_file`/`write_file` 修改 MEMORY.md 后，下一次 LLM 调用就能看到更新
- MEMORY.md 无论多大都会完整注入 system prompt——没有截断或摘要

### 2.3 写入路径

MEMORY.md 有两个写入来源：

**1. 代理主动写入**——代理在对话中判断有重要信息需要记住，调用 `edit_file`/`write_file` 直接修改。这由 memory 技能（`always=true`，始终注入 system prompt）引导：

```markdown
## When to Update MEMORY.md

Write important facts immediately using `edit_file` or `write_file`:

- User preferences ("I prefer dark mode")
- Project context ("The API uses OAuth2")
- Relationships ("Alice is the project lead")
```

**2. 自动巩固写入**——当会话 token 超标时，MemoryConsolidator 调用 LLM 自动提取长期事实并更新。详见第五章。

### 2.4 MEMORY.md 在最终 prompt 中的位置

```
[system] # nanobot 🐈 (identity)
         ---
         ## AGENTS.md / SOUL.md / USER.md / TOOLS.md (bootstrap files)
         ---
         # Memory                              ← 在这里
         ## Long-term Memory
         (MEMORY.md 的全部内容)
         ---
         # Active Skills (always=true 技能全文)
         ---
         # Skills (XML 摘要索引)

[user]   (history msg 1)
[assistant] (history msg 2)
...
[user]   [Runtime Context]\n\n(当前用户消息)
```

位于 bootstrap files 之后、skills 之前。

---

## 三、归档层：历史日志（HISTORY.md）

### 3.1 设计定位

HISTORY.md 是一个**只追加、不加载**的日志文件。它**不注入 system prompt**——这是和 MEMORY.md 最本质的区别。

代理需要回忆历史事件时，通过 `exec` 工具执行 `grep` 命令搜索：

```bash
grep -i "keyword" memory/HISTORY.md
```

### 3.2 格式规范

每条记录以 `[YYYY-MM-DD HH:MM]` 开头，方便 grep：

```
[2026-03-13 14:30] 用户询问了 Python 异步编程的最佳实践，代理通过 web_search
搜索了 3 个来源并汇总了关键点。用户表示满意，特别关注了 asyncio.gather 的用法。

[2026-03-13 15:00] 用户要求分析 /var/log/app.log，代理使用 read_file 读取日志
发现 3 个 ConnectionError 和 1 个 TimeoutError，建议增加重试逻辑。
```

### 3.3 写入来源

只有一个来源：MemoryConsolidator 的巩固过程。`MemoryStore.append_history()` 以追加模式写入：

```python
# memory.py:94-96
def append_history(self, entry: str) -> None:
    with open(self.history_file, "a", encoding="utf-8") as f:
        f.write(entry.rstrip() + "\n\n")
```

### 3.4 为什么分两层？

| 维度     | MEMORY.md                        | HISTORY.md                     |
| -------- | -------------------------------- | ------------------------------ |
| 加载方式 | 注入 system prompt（每次）       | 不加载（需 grep 搜索）         |
| 内容性质 | 结构化事实（用户偏好、项目信息） | 时间线事件（什么时候做了什么） |
| 更新方式 | 整体覆写（保留最新状态）         | 只追加（不修改历史记录）       |
| 大小影响 | 直接占用 prompt token            | 不占用 token                   |
| 适用场景 | "用户喜欢暗色模式"               | "3月13日用户问了异步编程"      |

这种设计使得**常用事实零成本访问**（MEMORY.md），而**历史细节不浪费 token 但可按需查询**（HISTORY.md）。

---

## 四、短期层：会话管理（Session）

### 4.1 Session 数据结构

```python
# session/manager.py
@dataclass
class Session:
    key: str                          # "telegram:789"
    messages: list[dict[str, Any]]    # 全部消息（append-only）
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]
    last_consolidated: int = 0        # 已巩固到此索引的消息
```

**关键设计：append-only**。消息一旦追加就不删除。巩固只是移动 `last_consolidated` 指针，让 `get_history()` 跳过已巩固的消息。

```
messages = [msg0, msg1, msg2, msg3, msg4, msg5, msg6, msg7, msg8]
                                  ↑                              ↑
                          last_consolidated=4              len=9

get_history() 返回 → [msg4, msg5, msg6, msg7, msg8]
                      (从 last_consolidated 开始)
```

### 4.2 为什么 append-only？

源码注释（`session/manager.py:23-25`）直接解释了原因：

> Messages are append-only for **LLM cache efficiency**.
> The consolidation process writes summaries to MEMORY.md/HISTORY.md
> but does **NOT modify** the messages list or get_history() output.

LLM 提供商（尤其是 Anthropic）支持 **prompt caching**——如果前面的消息没变，服务端可以复用缓存的 KV 状态。append-only 保证了历史消息在会话生命周期内不变，最大化 cache 命中率。

### 4.3 get_history() 的对齐逻辑

```python
# session/manager.py:46-64
def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
    unconsolidated = self.messages[self.last_consolidated:]
    sliced = unconsolidated[-max_messages:]

    # Drop leading non-user messages to avoid orphaned tool_result blocks
    for i, m in enumerate(sliced):
        if m.get("role") == "user":
            sliced = sliced[i:]
            break

    out = []
    for m in sliced:
        entry = {"role": m["role"], "content": m.get("content", "")}
        for k in ("tool_calls", "tool_call_id", "name"):
            if k in m:
                entry[k] = m[k]
        out.append(entry)
    return out
```

三步处理：

1. **取未巩固部分**：`messages[last_consolidated:]`
2. **对齐到 user turn**：丢弃开头的非 user 消息，避免孤立的 `tool_result` 或 `assistant` 消息（这些消息缺少对应的 `tool_calls`，会导致 LLM API 400 错误）
3. **清理 key**：只保留标准 key（role, content, tool_calls, tool_call_id, name），去掉 timestamp 等持久化字段

### 4.4 JSONL 持久化

```python
# session/manager.py:163-178 — SessionManager.save()
def save(self, session: Session) -> None:
    path = self._get_session_path(session.key)
    with open(path, "w", encoding="utf-8") as f:
        # 第一行：元数据
        metadata_line = {
            "_type": "metadata",
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "last_consolidated": session.last_consolidated    # ← 巩固指针持久化
        }
        f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
        # 后续行：每条消息一行
        for msg in session.messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
```

文件结构示例（`sessions/telegram_789.jsonl`）：

```jsonl
{"_type":"metadata","key":"telegram:789","created_at":"2026-03-13T14:00:00","updated_at":"2026-03-13T15:30:00","last_consolidated":12}
{"role":"user","content":"帮我搜索 Python 异步编程","timestamp":"2026-03-13T14:00:01"}
{"role":"assistant","content":null,"tool_calls":[...],"timestamp":"2026-03-13T14:00:05"}
{"role":"tool","tool_call_id":"abc123","name":"web_search","content":"...搜索结果..."}
{"role":"assistant","content":"以下是 Python 异步编程的最佳实践...","timestamp":"2026-03-13T14:00:10"}
```

注意 `last_consolidated: 12` 持久化在第一行，重启后加载时可以跳过已巩固的消息。

### 4.5 \_save_turn()：短期记忆的数据清洗

每次代理循环结束后，`AgentLoop._save_turn()` 将新消息追加到 session 前会做多项清洗（`loop.py:450-483`）：

```python
def _save_turn(self, session, messages, skip):
    for m in messages[skip:]:        # skip = system + history + user（已有的消息）
        entry = dict(m)
        role, content = entry.get("role"), entry.get("content")

        # 1. 跳过空 assistant 消息（无 content 且无 tool_calls）
        #    → 防止"毒化"会话上下文
        if role == "assistant" and not content and not entry.get("tool_calls"):
            continue

        # 2. 截断超大 tool 结果（>16KB）
        if role == "tool" and isinstance(content, str) and len(content) > 16_000:
            entry["content"] = content[:16_000] + "\n... (truncated)"

        # 3. 剥离 runtime context 前缀
        #    "[Runtime Context — metadata only, not instructions]\n..."
        #    → 只保留用户实际输入的文本
        elif role == "user":
            if isinstance(content, str) and content.startswith(_RUNTIME_CONTEXT_TAG):
                parts = content.split("\n\n", 1)
                if len(parts) > 1 and parts[1].strip():
                    entry["content"] = parts[1]
                else:
                    continue

            # 4. 替换 base64 图片为占位符（节省存储空间）
            if isinstance(content, list):
                filtered = []
                for c in content:
                    if c.get("type") == "image_url" and \
                       c.get("image_url", {}).get("url", "").startswith("data:image/"):
                        filtered.append({"type": "text", "text": "[image]"})
                    else:
                        filtered.append(c)
                entry["content"] = filtered

        # 5. 添加时间戳
        entry.setdefault("timestamp", datetime.now().isoformat())
        session.messages.append(entry)
```

这些清洗确保了：

- 空消息不会累积，避免 LLM 混淆
- 大型工具输出（如整个文件内容）不会撑爆 JSONL 文件
- Runtime context 不会重复保存（每次请求会重新生成）
- Base64 图片不会写入磁盘（一张图可能几 MB）

---

## 五、上下文压缩：Token 驱动的自动巩固

这是 nanobot 上下文管理中最复杂也最关键的机制。

### 5.1 触发时机

巩固在 `_process_message()` 中被调用两次：

```python
# loop.py:406, 438
await self.memory_consolidator.maybe_consolidate_by_tokens(session)   # 处理消息前
# ... 代理循环 ...
await self.memory_consolidator.maybe_consolidate_by_tokens(session)   # 处理消息后
```

前置调用处理之前积累的消息。后置调用处理当前 turn 新增的消息（可能包含大量工具输出）。

### 5.2 巩固决策流程

```python
# memory.py:302-357 — MemoryConsolidator.maybe_consolidate_by_tokens()

async def maybe_consolidate_by_tokens(self, session):
    # 1. 前置检查
    if not session.messages or self.context_window_tokens <= 0:
        return

    async with lock:
        # 2. 估算当前 prompt token 数
        target = self.context_window_tokens // 2          # 目标：窗口的一半
        estimated, source = self.estimate_session_prompt_tokens(session)

        # 3. 判断是否需要巩固
        if estimated < self.context_window_tokens:
            return                                         # 没超标，不需要

        # 4. 巩固循环（最多 5 轮）
        for round_num in range(5):
            if estimated <= target:
                return                                     # 已降到目标以下

            # 5. 找到切分边界
            boundary = self.pick_consolidation_boundary(session, estimated - target)
            if boundary is None:
                return                                     # 找不到安全边界

            # 6. 执行巩固
            chunk = session.messages[session.last_consolidated:boundary[0]]
            if not await self.consolidate_messages(chunk):
                return                                     # 巩固失败，停止

            # 7. 移动指针，保存
            session.last_consolidated = boundary[0]
            self.sessions.save(session)

            # 8. 重新估算，决定是否需要下一轮
            estimated, source = self.estimate_session_prompt_tokens(session)
```

完整流程图：

```
估算 prompt tokens
        │
        ├─ < context_window_tokens → 不巩固 ✓
        │
        └─ >= context_window_tokens → 需要巩固
              │
              ▼
          target = window / 2
              │
          ┌─→ 轮次 < 5 且 estimated > target
          │       │
          │       ├─ pick_consolidation_boundary()
          │       │   扫描 messages[last_consolidated:]
          │       │   在 role="user" 处设置边界
          │       │   累积 token 直到够 (estimated - target)
          │       │   返回 (end_idx, removed_tokens)
          │       │
          │       ├─ chunk = messages[last_consolidated:end_idx]
          │       │
          │       ├─ consolidate_messages(chunk) → MemoryStore.consolidate()
          │       │   │
          │       │   ├─ 调用 LLM 总结 → save_memory 工具
          │       │   ├─ history_entry → HISTORY.md (追加)
          │       │   └─ memory_update → MEMORY.md (覆写)
          │       │
          │       ├─ last_consolidated = end_idx
          │       ├─ 保存 session
          │       ├─ 重新估算 tokens
          │       └─ continue ───────────────────┐
          │                                      │
          └──────────────────────────────────────┘
```

### 5.3 切分边界的选择

```python
# memory.py:254-274
def pick_consolidation_boundary(self, session, tokens_to_remove):
    start = session.last_consolidated
    removed_tokens = 0
    last_boundary = None

    for idx in range(start, len(session.messages)):
        message = session.messages[idx]
        if idx > start and message.get("role") == "user":
            last_boundary = (idx, removed_tokens)
            if removed_tokens >= tokens_to_remove:
                return last_boundary
        removed_tokens += estimate_message_tokens(message)

    return last_boundary
```

关键规则：**只在 user 消息处切分**。

为什么不能在 assistant 或 tool 消息处切？因为 LLM API 要求 `tool_calls` 和 `tool` 消息成对出现——如果在中间切断，会产生孤立的 tool_result 导致 400 错误。在 user 消息处切，保证了每个工具调用周期的完整性。

### 5.4 Token 估算机制

估算分两级（`helpers.py:151-170`）：

```python
def estimate_prompt_tokens_chain(provider, model, messages, tools):
    # 1. 优先使用 provider 自带的计数器（如果有的话）
    provider_counter = getattr(provider, "estimate_prompt_tokens", None)
    if callable(provider_counter):
        tokens, source = provider_counter(messages, tools, model)
        if tokens > 0:
            return int(tokens), str(source)

    # 2. fallback 到 tiktoken (cl100k_base 编码)
    estimated = estimate_prompt_tokens(messages, tools)
    if estimated > 0:
        return int(estimated), "tiktoken"

    return 0, "none"
```

tiktoken 估算逻辑（`helpers.py:92-114`）：

```python
def estimate_prompt_tokens(messages, tools=None):
    enc = tiktoken.get_encoding("cl100k_base")
    parts = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    parts.append(part.get("text", ""))
    if tools:
        parts.append(json.dumps(tools, ensure_ascii=False))
    return len(enc.encode("\n".join(parts)))
```

估算的是**完整 prompt**的 token 数，包括：

- 所有消息的文本内容
- 多模态消息中的 text 块
- 工具定义的 JSON

**不包括** base64 图片——这是有意为之。图片在 `_save_turn` 时已被替换为 `[image]`，历史消息中不会有完整图片数据。

### 5.5 实际巩固过程：LLM 驱动的总结

```python
# memory.py:114-199 — MemoryStore.consolidate()

async def consolidate(self, messages, provider, model):
    current_memory = self.read_long_term()

    # 1. 构建巩固 prompt
    prompt = f"""Process this conversation and call the save_memory tool...

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{self._format_messages(messages)}"""

    chat_messages = [
        {"role": "system", "content": "You are a memory consolidation agent..."},
        {"role": "user", "content": prompt},
    ]

    # 2. 调用 LLM，强制使用 save_memory 工具
    forced = {"type": "function", "function": {"name": "save_memory"}}
    response = await provider.chat_with_retry(
        messages=chat_messages,
        tools=_SAVE_MEMORY_TOOL,
        model=model,
        tool_choice=forced,
    )

    # 3. 提取输出
    args = response.tool_calls[0].arguments
    #   history_entry: "[2026-03-13 14:30] 用户询问了..."
    #   memory_update: "# Long-term Memory\n- 用户对 Python 感兴趣\n..."

    # 4. 写入文件
    self.append_history(entry)          # → HISTORY.md (追加)
    if update != current_memory:
        self.write_long_term(update)    # → MEMORY.md (覆写)
```

巩固代理（consolidation agent）是一个**独立的 LLM 调用**，不共享主代理的上下文：

- 系统提示：`"You are a memory consolidation agent."`
- 输入：当前 MEMORY.md 内容 + 待巩固的对话片段
- 工具：只有一个 `save_memory`，强制调用
- 输出两部分：
  - `history_entry`：一段总结性文字，追加到 HISTORY.md
  - `memory_update`：更新后的完整 MEMORY.md 内容

### 5.6 save_memory 工具定义

```python
_SAVE_MEMORY_TOOL = [{
    "type": "function",
    "function": {
        "name": "save_memory",
        "parameters": {
            "type": "object",
            "properties": {
                "history_entry": {
                    "type": "string",
                    "description": "A paragraph summarizing key events/decisions/topics. "
                                   "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search.",
                },
                "memory_update": {
                    "type": "string",
                    "description": "Full updated long-term memory as markdown. Include all existing "
                                   "facts plus new ones. Return unchanged if nothing new.",
                },
            },
            "required": ["history_entry", "memory_update"],
        },
    },
}]
```

description 中的措辞很精心：

- history_entry 要求 `"Start with [YYYY-MM-DD HH:MM]"` — 确保 grep 友好
- history_entry 要求 `"Include detail useful for grep search"` — 引导 LLM 包含可搜索的关键词
- memory_update 要求 `"Include all existing facts plus new ones"` — 确保旧事实不丢失
- memory_update 要求 `"Return unchanged if nothing new"` — 避免不必要的覆写

### 5.7 降级机制：raw archive

当巩固连续失败 3 次时，降级为原始归档：

```python
# memory.py:201-219
def _fail_or_raw_archive(self, messages):
    self._consecutive_failures += 1
    if self._consecutive_failures < 3:      # 前 2 次失败：返回 False，稍后重试
        return False
    self._raw_archive(messages)             # 第 3 次失败：直接 dump
    self._consecutive_failures = 0
    return True

def _raw_archive(self, messages):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    self.append_history(
        f"[{ts}] [RAW] {len(messages)} messages\n"
        f"{self._format_messages(messages)}"
    )
```

降级后的 HISTORY.md 条目以 `[RAW]` 标记，格式为原始消息列表。这种设计的哲学是：**宁可冗余也不丢失信息**。

### 5.8 tool_choice 兼容性

部分 LLM 提供商不支持 `tool_choice=forced`。代码通过错误检测自动降级：

```python
# memory.py:147-156
if response.finish_reason == "error" and _is_tool_choice_unsupported(response.content):
    logger.warning("Forced tool_choice unsupported, retrying with auto")
    response = await provider.chat_with_retry(
        messages=chat_messages,
        tools=_SAVE_MEMORY_TOOL,
        model=model,
        tool_choice="auto",       # 降级为 auto
    )
```

检测关键词（`memory.py:61-66`）：

```python
_TOOL_CHOICE_ERROR_MARKERS = (
    "tool_choice",
    "toolchoice",
    "does not support",
    'should be ["none", "auto"]',
)
```

### 5.9 巩固锁

```python
# memory.py:244
self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

def get_lock(self, session_key: str) -> asyncio.Lock:
    return self._locks.setdefault(session_key, asyncio.Lock())
```

使用 `WeakValueDictionary`：当某个 session_key 的锁不再被持有时，自动从字典中移除。这避免了长期运行时锁对象的内存泄漏。

---

## 六、上下文组装：ContextBuilder

### 6.1 build_messages() —— 最终发给 LLM 的消息列表

```python
# context.py:121-145
def build_messages(self, history, current_message, skill_names=None,
                   media=None, channel=None, chat_id=None):
    runtime_ctx = self._build_runtime_context(channel, chat_id)
    user_content = self._build_user_content(current_message, media)

    # 合并 runtime context 和用户内容到同一条 user 消息
    # 避免连续 same-role 消息（部分提供商会拒绝）
    if isinstance(user_content, str):
        merged = f"{runtime_ctx}\n\n{user_content}"
    else:
        merged = [{"type": "text", "text": runtime_ctx}] + user_content

    return [
        {"role": "system", "content": self.build_system_prompt(skill_names)},
        *history,
        {"role": "user", "content": merged},
    ]
```

最终结构：

```
messages[0]    = system prompt（身份 + bootstrap + 记忆 + 技能）
messages[1..N] = history（未巩固的历史消息）
messages[N+1]  = 当前用户消息（runtime context + 文本 + 可选图片）
```

### 6.2 Runtime Context —— 防注入设计

```python
# context.py:99-107
_RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"

@staticmethod
def _build_runtime_context(channel, chat_id):
    now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    tz = time.strftime("%Z") or "UTC"
    lines = [f"Current Time: {now} ({tz})"]
    if channel and chat_id:
        lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
    return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)
```

Runtime context 包含当前时间、渠道、chat_id 等元数据。`"metadata only, not instructions"` 这个标签告诉 LLM 这段内容不应被视为用户指令——这是一种轻量级的 **prompt injection 防护**。

**在持久化时被剥离**：`_save_turn()` 中检测到 `_RUNTIME_CONTEXT_TAG` 前缀会将其移除，只保留用户实际输入。这是因为 runtime context 每次请求会重新生成（时间会变），保存它会造成冗余和混乱。

### 6.3 System Prompt 的构成层次

```python
# context.py:27-54
def build_system_prompt(self, skill_names=None):
    parts = [self._get_identity()]                    # 层 1：身份

    bootstrap = self._load_bootstrap_files()          # 层 2：Bootstrap 文件
    if bootstrap:
        parts.append(bootstrap)

    memory = self.memory.get_memory_context()          # 层 3：长期记忆
    if memory:
        parts.append(f"# Memory\n\n{memory}")

    always_skills = self.skills.get_always_skills()    # 层 4：始终加载的技能
    if always_skills:
        always_content = self.skills.load_skills_for_context(always_skills)
        parts.append(f"# Active Skills\n\n{always_content}")

    skills_summary = self.skills.build_skills_summary() # 层 5：技能索引
    if skills_summary:
        parts.append(f"# Skills\n\n...{skills_summary}")

    return "\n\n---\n\n".join(parts)                   # 用 --- 分隔
```

五层结构按优先级排列：

| 层                 | 来源                | 变化频率 | 对 prompt cache 的影响 |
| ------------------ | ------------------- | -------- | ---------------------- |
| 1. Identity        | 硬编码 + 运行时计算 | 几乎不变 | 高 cache 命中          |
| 2. Bootstrap Files | workspace/\*.md     | 偶尔变   | 高 cache 命中          |
| 3. Memory          | memory/MEMORY.md    | 巩固时变 | 中等 cache 命中        |
| 4. Active Skills   | skills/\*/SKILL.md  | 很少变   | 高 cache 命中          |
| 5. Skills Summary  | 技能目录扫描        | 很少变   | 高 cache 命中          |

system prompt 的前几层很少变化，这与 Anthropic 的 prompt caching 策略配合得很好——越前面的内容越稳定，cache 命中率越高。

### 6.4 Bootstrap Files 的作用

```python
BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md"]
```

| 文件        | 用途             | 默认内容                                       |
| ----------- | ---------------- | ---------------------------------------------- |
| `AGENTS.md` | 代理行为指令     | 通用行为准则、定时任务指引、心跳任务指引       |
| `SOUL.md`   | 人格设定         | 性格（友好、简洁、好奇）、价值观、沟通风格     |
| `USER.md`   | 用户画像         | 姓名、时区、语言、偏好、工作背景（待用户填写） |
| `TOOLS.md`  | 工具使用注意事项 | exec 安全限制、cron 使用指引                   |

这些文件由模板系统初始化，用户可自由编辑。它们的定位是**可定制的系统提示扩展**——不需要改代码就能调整代理行为。

---

## 七、Prompt Caching 优化

### 7.1 Anthropic cache_control 注入

```python
# litellm_provider.py:128-152
def _apply_cache_control(self, messages, tools):
    new_messages = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg["content"]
            if isinstance(content, str):
                # 将 string content 转为 list，添加 cache_control
                new_content = [{
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"}
                }]
            else:
                # list content：在最后一个 block 上加 cache_control
                new_content = list(content)
                new_content[-1] = {**new_content[-1], "cache_control": {"type": "ephemeral"}}
            new_messages.append({**msg, "content": new_content})
        else:
            new_messages.append(msg)

    # 工具定义的最后一个也加 cache_control
    if tools:
        new_tools = list(tools)
        new_tools[-1] = {**new_tools[-1], "cache_control": {"type": "ephemeral"}}

    return new_messages, new_tools
```

这告诉 Anthropic API：system prompt 和工具定义可以被缓存。由于这些内容在会话中很少变化，cache 命中可以**节约 90%+ 的 prompt token 计费**。

### 7.2 缓存友好的设计决策汇总

| 决策                                             | 缓存影响                         |
| ------------------------------------------------ | -------------------------------- |
| System prompt 在会话中几乎不变                   | system 层 cache 命中率高         |
| Session messages 是 append-only                  | history 层前缀不变，cache 可复用 |
| Runtime context 在 user 消息内部（不是独立消息） | 不影响 history 消息的 cache      |
| Bootstrap files 很少修改                         | system prompt 前段稳定           |
| MEMORY.md 只在巩固时变化                         | 大部分请求 system prompt 不变    |

---

## 八、/new 命令：会话重置

```python
# loop.py:374-394
if cmd == "/new":
    # 1. 先巩固所有未归档消息
    if not await self.memory_consolidator.archive_unconsolidated(session):
        return "Memory archival failed, session not cleared."

    # 2. 清空会话
    session.clear()
    self.sessions.save(session)
    self.sessions.invalidate(session.key)
    return "New session started."
```

`archive_unconsolidated()` 确保 `/new` 之前的对话不会丢失——它被完整巩固到 MEMORY.md + HISTORY.md 后才清空 session。

---

## 九、HeartbeatService：定时上下文唤醒

心跳服务是一个与记忆系统配合的定时机制（`heartbeat/service.py`）：

```
每 30 分钟（可配置）
    │
    ├─ 读取 workspace/HEARTBEAT.md
    │
    ├─ Phase 1（决策）：
    │   调用 LLM + heartbeat 工具
    │   → action="skip"：无任务，跳过
    │   → action="run" + tasks="..."：有任务
    │
    └─ Phase 2（执行）：
        通过 agent.process_direct(tasks) 走完整代理循环
        → 可以读写文件、搜索网络、更新 MEMORY.md
        → 结果发送到用户的聊天渠道
```

HEARTBEAT.md 是代理可自主编辑的"待办事项清单"——代理在对话中可以通过 `edit_file` 往里添加定期任务。心跳服务定期检查这个文件，有任务就执行。

---

## 十、Memory 技能：引导代理使用记忆系统

`skills/memory/SKILL.md` 标记为 `always: true`，其内容始终注入 system prompt：

```markdown
# Memory

## Structure

- `memory/MEMORY.md` — Long-term facts. Always loaded into your context.
- `memory/HISTORY.md` — Append-only event log. NOT loaded into context.
  Search it with grep-style tools. Each entry starts with [YYYY-MM-DD HH:MM].

## Search Past Events

- Small HISTORY.md: use `read_file`, then search in-memory
- Large HISTORY.md: use `exec` tool for targeted search
  `grep -i "keyword" memory/HISTORY.md`

## When to Update MEMORY.md

Write important facts immediately using `edit_file` or `write_file`:

- User preferences ("I prefer dark mode")
- Project context ("The API uses OAuth2")
- Relationships ("Alice is the project lead")

## Auto-consolidation

Old conversations are automatically summarized and appended to HISTORY.md
when the session grows large. Long-term facts are extracted to MEMORY.md.
You don't need to manage this.
```

这段技能文本告诉代理：

1. 记忆系统的结构和两个文件的用途
2. 如何搜索历史（小文件 read_file，大文件 grep）
3. 何时主动更新 MEMORY.md
4. 自动巩固的存在（不需要手动管理）

---

## 十一、完整数据流总览

```
                   ┌──────────────────────────┐
                   │   用户发送消息            │
                   └────────────┬─────────────┘
                                │
                   ┌────────────▼─────────────┐
                   │  AgentLoop._process_msg() │
                   └────────────┬─────────────┘
                                │
              ┌─────────────────▼─────────────────┐
              │ maybe_consolidate_by_tokens()      │ ← 前置巩固
              │ if prompt_tokens > context_window: │
              │   pick_boundary → consolidate      │
              │   → HISTORY.md (追加)              │
              │   → MEMORY.md (覆写)               │
              │   → last_consolidated 前移          │
              └─────────────────┬─────────────────┘
                                │
              ┌─────────────────▼─────────────────┐
              │ ContextBuilder.build_messages()    │
              │                                    │
              │ system = identity                  │
              │        + AGENTS.md/SOUL.md/...     │
              │        + MEMORY.md ←────────────── │ ← 长期记忆注入
              │        + always skills             │
              │        + skills summary            │
              │                                    │
              │ history = session.get_history()     │ ← 短期记忆
              │           (last_consolidated 之后)  │
              │                                    │
              │ user = runtime_ctx + 当前消息       │ ← 瞬时层
              └─────────────────┬─────────────────┘
                                │
              ┌─────────────────▼─────────────────┐
              │ provider.chat_with_retry()         │
              │ ├─ _apply_cache_control()          │ ← prompt caching
              │ ├─ _sanitize_messages()            │
              │ └─ litellm.acompletion()           │
              └─────────────────┬─────────────────┘
                                │
                        ┌───────▼───────┐
                        │  有 tool_calls │
                        │  → 执行工具    │
                        │  → 追加消息    │
                        │  → 再次调用 LLM │
                        │  (循环)        │
                        └───────┬───────┘
                                │
              ┌─────────────────▼─────────────────┐
              │ _save_turn()                       │
              │ ├─ 跳过空 assistant 消息            │
              │ ├─ 截断 >16KB 的 tool 结果          │
              │ ├─ 剥离 runtime context 前缀        │ ← 数据清洗
              │ ├─ 替换 base64 图片为 [image]       │
              │ └─ 添加 timestamp → session.append  │
              └─────────────────┬─────────────────┘
                                │
              ┌─────────────────▼─────────────────┐
              │ sessions.save(session)             │ ← JSONL 持久化
              └─────────────────┬─────────────────┘
                                │
              ┌─────────────────▼─────────────────┐
              │ maybe_consolidate_by_tokens()      │ ← 后置巩固
              └─────────────────┬─────────────────┘
                                │
                   ┌────────────▼─────────────┐
                   │  发送回复给用户            │
                   └──────────────────────────┘
```

---

## 十二、设计权衡与思考

### 优势

- **token 驱动巩固**比基于消息条数更精确——一条带有大量工具输出的消息可能比 10 条纯文本消息占更多 token
- **两层记忆**分离了"总是需要"和"偶尔搜索"的信息，避免 token 浪费
- **append-only session** 对 prompt caching 非常友好
- **user turn 对齐切分**避免了破坏工具调用的完整性
- **降级机制**确保巩固失败不会导致信息丢失
- **runtime context 剥离**避免了冗余持久化

### 局限

- **MEMORY.md 无大小限制**：如果长期记忆无限增长，最终也会撑满 prompt。目前没有对 MEMORY.md 本身做截断或摘要的机制
- **全局 processing_lock**：巩固过程中（涉及 LLM 调用，可能耗时数秒）会阻塞其他会话的消息处理
- **tiktoken 估算与实际 token 有误差**：cl100k_base 不能精确反映所有提供商的 tokenizer，但作为近似值足够驱动巩固决策
- **JSONL 文件只增不减**：即使消息已巩固，它们仍保留在 JSONL 文件中（为了 append-only 设计），长期运行后文件可能很大
- **巩固质量依赖 LLM**：如果 LLM 总结不准确，可能丢失重要细节或引入错误到 MEMORY.md

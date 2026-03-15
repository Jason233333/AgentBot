# MEMORY.md 无限增长问题

> 项目：nanobot (HKUDS/nanobot)
> 代码版本：0.1.4.post4: 65cbd7eb78672e226a8108c81da3ed8ce50ab192
> 涉及文件：
>
> - `nanobot/agent/context.py:35-37`（注入点）
> - `nanobot/agent/memory.py:86-101`（读取逻辑）
> - `nanobot/agent/memory.py:114-199`（巩固写入逻辑）

---

## 问题描述

MEMORY.md 作为长期记忆文件，每次 LLM 请求时被**完整注入 system prompt**，没有任何大小限制：

```python
# context.py:35-37
memory = self.memory.get_memory_context()
if memory:
    parts.append(f"# Memory\n\n{memory}")
```

MEMORY.md 的增长来自两个方向：

1. **自动巩固写入**：`MemoryConsolidator` 每次巩固时调用 LLM，LLM 输出 `memory_update` 字段覆写 MEMORY.md。虽然 prompt 中要求 `"Include all existing facts plus new ones"`，但随着使用时间增长，累积的事实越来越多。
2. **代理主动写入**：代理在对话中用 `edit_file`/`write_file` 直接向 MEMORY.md 追加信息。

### 可能导致的后果

| 阶段 | MEMORY.md 大小      | 影响                                                            |
| ---- | ------------------- | --------------------------------------------------------------- |
| 初期 | 几百 token          | 无影响                                                          |
| 中期 | 数千 token          | system prompt 膨胀，history 可用空间被压缩，巩固频率增加        |
| 后期 | 上万 token          | system prompt 本身可能接近或超过 context window，巩固也无法缓解 |
| 极端 | 超过 context window | system prompt 无法完整发送，LLM 调用报错，系统崩溃              |

### 当前的"软制约"（不可靠）

- 巩固 prompt 的 `memory_update` 描述要求 LLM 输出完整的更新后记忆，倾向于保持紧凑——但 LLM 不一定遵守
- 代理自身可以主动精简 MEMORY.md——但没有机制触发这个行为
- `/new` 命令清空 session 但**不清空 MEMORY.md**——长期记忆的积累不受 session 重置影响

---

## 改进建议

### 方案 A：硬性 token 上限 + 自动压缩

在 `ContextBuilder.build_system_prompt()` 中对 MEMORY.md 内容做 token 估算，超过阈值时触发一次专门的"记忆压缩"LLM 调用：

```python
# 伪代码
memory_content = self.memory.read_long_term()
memory_tokens = estimate_tokens(memory_content)
MAX_MEMORY_TOKENS = self.context_window_tokens // 4  # 例如上下文窗口的 25%

if memory_tokens > MAX_MEMORY_TOKENS:
    compressed = await self._compress_memory(memory_content, MAX_MEMORY_TOKENS)
    self.memory.write_long_term(compressed)
    memory_content = compressed
```

压缩 LLM 的 prompt 可以要求：

- 保留最重要/最近的事实
- 合并重复信息
- 删除过时的条目
- 控制输出在 token 上限以内

**优势**：硬性保证不超标
**代价**：额外的 LLM 调用成本；可能丢失信息

### 方案 B：分层记忆（热/冷分离）

将 MEMORY.md 拆分为两个文件：

- `MEMORY_HOT.md`：最近/高频事实，注入 system prompt，有 token 上限
- `MEMORY_COLD.md`：旧/低频事实，不注入 prompt，代理可按需 `read_file` 查询

巩固时 LLM 负责判断哪些事实应该保留在 HOT 层。

**优势**：兼顾上下文大小和信息保留
**代价**：实现复杂度增加；需要修改巩固逻辑和 memory 技能

### 方案 C：巩固时强制精简

修改 `_SAVE_MEMORY_TOOL` 的 `memory_update` 描述，增加 token 上限约束：

```python
"description": "Full updated long-term memory as markdown. "
               "MUST be under 2000 tokens. "
               "Prioritize recent and frequently referenced facts. "
               "Remove outdated or redundant entries."
```

**优势**：最小改动，利用 LLM 自身判断
**代价**：依赖 LLM 遵守指令，不可靠；硬编码 token 上限不灵活

### 方案 D：监控告警

不主动压缩，但在 `build_system_prompt()` 中加入 token 监控，当 MEMORY.md 超过阈值时 log warning 并通知用户：

```python
if memory_tokens > WARNING_THRESHOLD:
    logger.warning("MEMORY.md is {} tokens, consider manual cleanup", memory_tokens)
```

**优势**：零风险，不改变现有行为
**代价**：需要人工介入

### 推荐

短期采用 **方案 D**（监控告警），中期实现 **方案 A**（硬性上限 + 自动压缩）。方案 B 适合需求明确后的长期演进。

---

## 关联问题

### Session JSONL 文件无限增长

同样位于 `session/manager.py`，Session 的 messages 是 append-only 的，即使做了 consolidate 也不删除旧消息。JSONL 文件只增不减，唯一清空方式是 `/new` 命令。

长期运行的 Session 可能产生很大的 JSONL 文件。虽然对运行时性能影响有限（`get_history()` 只返回 `messages[last_consolidated:]`），但：

- 磁盘空间持续消耗
- 进程启动时加载全量消息到内存

**改进建议**：在 `SessionManager.save()` 中，可选择只保留 `messages[last_consolidated:]` + 元数据写入文件，丢弃已巩固的消息。这会牺牲 prompt cache 的理论最优性，但大幅减少磁盘和内存消耗。或者定期将已巩固消息归档到单独的 `.archive.jsonl` 文件。

### HISTORY.md 无限增长

HISTORY.md 是追加写入的，永远不会被清理。长期使用后文件可能变得很大，`grep` 搜索会变慢。

**改进建议**：按年/月分割（如 `HISTORY_2026-03.md`），或设置文件大小上限后自动 rotate。

# Zero-Token 上下文管理优化

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**目标:** 修复 zero-token 模式下模型遗忘系统提示和上文信息的问题，通过周期性重新注入上下文来保持模型记忆。

**架构:** 在 full_prompt 调用时缓存系统提示；构建精简版"上下文提醒"（身份 + 规则 + 记忆摘要 + 工具列表）；在每次非 full_prompt 消息中注入提醒；每 N 轮做一次深度刷新；将会话轮转阈值从 20 降到 12。

**技术栈:** Python, ClaudeWebProvider, pytest

---

## 问题分析

`_convert_messages` 有 3 条路径：

| 路径                            | 触发条件                      | 包含系统提示？ | 包含上下文？                    |
| ------------------------------- | ----------------------------- | -------------- | ------------------------------- |
| `full_prompt=True`              | 新建 conversation             | 是             | 完整历史                        |
| `full_prompt=False`（用户消息） | 已有 conversation，新用户轮次 | 否             | 仅最后一条用户消息 + 工具名提示 |
| `is_continuation`               | agent loop 中的工具结果       | 否             | 仅工具结果 + "Please proceed"   |

路径 2 和 3 **完全不包含身份/行为上下文**，导致模型快速遗忘自己是谁、该遵循什么规则、有什么记忆。

## 设计方案

### 精简上下文提醒（每次非 full_prompt 轮次注入）

```
[CONTEXT REMINDER]
You are nanobot, a helpful AI assistant. Your workspace: /path/to/workspace.

Key rules:
- State intent before tool calls, never predict results
- Read files before modifying them
- Reply directly with text; use 'message' tool only for specific channels

Memory: {MEMORY.md 前 300 字符}

Available tools: read_file, write_file, edit_file, ...
To call a tool: <tool_call id="unique_id" name="tool_name">{"param": "value"}</tool_call>
```

### 深度刷新（每 `_DEEP_REFRESH_INTERVAL` 轮注入）

与精简提醒相同位置，但注入完整系统提示而非精简版。比会话轮转代价小（不会丢弃服务端上下文）。

---

### Task 1: 缓存系统提示

**文件:**

- 修改: `nanobot/providers/claude_web_provider.py`（类属性 + `__init__`）

**Step 1: 添加 `_system_prompts` 缓存和常量**

修改类常量，降低轮转阈值，添加刷新间隔：

```python
# 替换现有常量（第 36-44 行）
_MAX_HISTORY_TURNS = 10
_CONV_ROTATION_TURNS = 12       # 原 20
_CONTEXT_REMINDER_INTERVAL = 1  # 每轮注入精简提醒
_DEEP_REFRESH_INTERVAL = 5      # 每 5 轮注入完整系统提示
```

在 `__init__` 的 `self._turn_counts` 之后添加：

```python
self._system_prompts: dict[str, str] = {}  # session_key → 缓存的系统提示
```

**Step 2: 在 `_chat_with_fallback` 中缓存系统提示**

在 `is_new_conversation` 判定之后，提取并缓存系统提示：

```python
# 在 _chat_with_fallback 中，is_new_conversation 设置之后：
if is_new_conversation:
    for msg in messages:
        if msg.get("role") == "system":
            self._system_prompts[session_key] = msg.get("content", "")
            break
```

**Step 3: 运行现有测试确认无回归**

运行: `uv run pytest tests/test_zero_token_flow.py -v`
预期: 全部 PASS（尚未改变行为）

**Step 4: 提交**

```bash
git add nanobot/providers/claude_web_provider.py
git commit -m "refactor(zero-token): cache system prompt per session, reduce rotation to 12 turns"
```

---

### Task 2: 构建上下文提醒方法

**文件:**

- 修改: `nanobot/providers/claude_web_provider.py`（新增方法）
- 测试: `tests/test_zero_token_flow.py`

**Step 1: 编写失败测试**

```python
class TestContextReminder:
    """测试 _build_context_reminder 精简上下文注入。"""

    def setup_method(self):
        self.provider = ClaudeWebProvider(default_model="claude-sonnet-4-6")

    def test_builds_condensed_reminder(self):
        system_prompt = (
            "# nanobot\n\nYou are nanobot, a helpful AI assistant.\n\n"
            "## Workspace\nYour workspace is at: /tmp/ws\n"
            "- Long-term memory: /tmp/ws/memory/MEMORY.md\n\n"
            "## nanobot Guidelines\n- State intent before tool calls.\n"
            "- Read files before modifying them.\n"
        )
        tools = [
            {"type": "function", "function": {"name": "read_file", "description": "Read"}},
            {"type": "function", "function": {"name": "exec", "description": "Run"}},
        ]
        reminder = self.provider._build_context_reminder(system_prompt, tools)

        assert "[CONTEXT REMINDER]" in reminder
        assert "nanobot" in reminder
        assert "read_file" in reminder
        assert "exec" in reminder
        assert "tool_call" in reminder
        # 应该是精简版，不是完整系统提示
        assert len(reminder) < len(system_prompt) + 200

    def test_reminder_without_tools(self):
        reminder = self.provider._build_context_reminder("You are nanobot.", None)
        assert "[CONTEXT REMINDER]" in reminder
        assert "nanobot" in reminder

    def test_reminder_empty_prompt(self):
        reminder = self.provider._build_context_reminder("", None)
        assert "[CONTEXT REMINDER]" in reminder
```

**Step 2: 运行测试确认失败**

运行: `uv run pytest tests/test_zero_token_flow.py::TestContextReminder -v`
预期: FAIL，`has no attribute '_build_context_reminder'`

**Step 3: 实现 `_build_context_reminder`**

在 `ClaudeWebProvider` 中添加方法：

```python
def _build_context_reminder(
    self,
    system_prompt: str,
    tools: list[dict[str, Any]] | None,
) -> str:
    """从缓存的系统提示构建精简上下文提醒。

    注入到非 full_prompt 消息中，防止模型遗忘身份、规则和可用工具。
    """
    parts = ["[CONTEXT REMINDER]"]

    # 提取身份信息：第一个有意义的段落
    if system_prompt:
        lines = system_prompt.split("\n")
        identity_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("# ") or stripped.startswith("## Runtime"):
                continue
            if stripped.startswith("## ") and identity_lines:
                break
            if stripped:
                identity_lines.append(stripped)
            if len(identity_lines) >= 3:
                break
        if identity_lines:
            parts.append(" ".join(identity_lines))
        else:
            parts.append(system_prompt[:300])

    # 提取关键行为规则（Guidelines 部分）
    if "Guidelines" in system_prompt:
        guidelines_start = system_prompt.find("Guidelines")
        guidelines_section = system_prompt[guidelines_start:]
        next_section = guidelines_section.find("\n## ", 1)
        if next_section > 0:
            guidelines_section = guidelines_section[:next_section]
        rules = [
            line.strip()
            for line in guidelines_section.split("\n")
            if line.strip().startswith("- ")
        ]
        if rules:
            parts.append("Key rules:\n" + "\n".join(rules))

    # 工具列表 + 调用格式
    if tools:
        tool_names = [
            (t.get("function", t) or {}).get("name", "?") for t in tools
        ]
        parts.append(
            "Available tools: " + ", ".join(tool_names)
            + '\nTo call a tool: <tool_call id="unique_id" name="tool_name">{"param": "value"}</tool_call>'
        )

    return "\n\n".join(parts)
```

**Step 4: 运行测试确认通过**

运行: `uv run pytest tests/test_zero_token_flow.py::TestContextReminder -v`
预期: PASS

**Step 5: 提交**

```bash
git add nanobot/providers/claude_web_provider.py tests/test_zero_token_flow.py
git commit -m "feat(zero-token): add _build_context_reminder for condensed context injection"
```

---

### Task 3: 在 continuation 和已有会话消息中注入上下文提醒

**文件:**

- 修改: `nanobot/providers/claude_web_provider.py`（`_convert_messages` + 调用方）
- 测试: `tests/test_zero_token_flow.py`

**Step 1: 编写失败测试**

在 `TestMessageConversion` 类中添加：

```python
def test_continuation_includes_context_reminder(self):
    """Continuation（工具结果）应包含上下文提醒。"""
    self.provider._system_prompts["test-key"] = "You are nanobot, a helpful AI assistant."
    messages = [
        {"role": "system", "content": "You are nanobot, a helpful AI assistant."},
        {"role": "user", "content": "Do stuff"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "t1", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "a", "name": "t1", "content": "result_a"},
    ]
    tools = [{"type": "function", "function": {"name": "t1", "description": "Tool 1"}}]
    prompt, _ = self.provider._convert_messages(
        messages, tools, full_prompt=False, session_key="test-key",
    )
    assert "[CONTEXT REMINDER]" in prompt
    assert "nanobot" in prompt
    assert "result_a" in prompt
```

**Step 2: 运行测试确认失败**

运行: `uv run pytest tests/test_zero_token_flow.py::TestMessageConversion::test_continuation_includes_context_reminder -v`
预期: FAIL

**Step 3: 修改 `_convert_messages` 注入上下文提醒**

1. 修改签名，添加 `session_key` 参数：

```python
def _convert_messages(
    self,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    full_prompt: bool = True,
    session_key: str = "",
) -> tuple[str, list[dict[str, Any]]]:
```

2. 替换现有 `tool_hint` 逻辑（第 308-317 行）为上下文提醒构建：

```python
# 为 continuation/已有会话路径构建上下文提醒
context_reminder = ""
if not full_prompt:
    cached_prompt = self._system_prompts.get(session_key, "")
    if not cached_prompt:
        # 回退：从当前消息中提取
        for msg in messages:
            if msg.get("role") == "system":
                cached_prompt = msg.get("content", "")
                break

    turn_count = self._turn_counts.get(session_key, 0)
    is_deep_refresh = turn_count > 0 and turn_count % self._DEEP_REFRESH_INTERVAL == 0

    if is_deep_refresh and cached_prompt:
        logger.info(
            "[zero-token] deep refresh at turn {} for session {}",
            turn_count, session_key,
        )
        context_reminder = "\n\n[SYSTEM PROMPT REFRESH — re-read carefully]\n\n" + cached_prompt
        if tools:
            tool_names = [
                (t.get("function", t) or {}).get("name", "?") for t in tools
            ]
            context_reminder += (
                '\n\nAvailable tools: ' + ", ".join(tool_names)
                + '\nTo call a tool: <tool_call id="unique_id" name="tool_name">{"param": "value"}</tool_call>'
            )
    else:
        context_reminder = "\n\n" + self._build_context_reminder(cached_prompt, tools)
```

3. 替换 continuation 路径中的 `tool_hint`（第 323 行）：

```python
return prompt + context_reminder, atts
```

4. 替换已有会话路径中的 `tool_hint`（第 331 行）：

```python
return (text or "") + context_reminder, msg_attachments
```

5. 更新 `_chat_with_fallback` 和 `_rebuild_conversation` 中的调用方，传入 `session_key`：

```python
# _chat_with_fallback 中：
prompt, attachments = self._convert_messages(
    messages, tools, full_prompt=is_new_conversation,
    session_key=session_key,
)

# _rebuild_conversation 中：
prompt, attachments = self._convert_messages(
    messages, tools, full_prompt=True,
    session_key=session_key,
)
```

**Step 4: 运行全部测试**

运行: `uv run pytest tests/test_zero_token_flow.py -v`
预期: 全部 PASS（可能需要微调旧测试的断言）

**Step 5: 提交**

```bash
git add nanobot/providers/claude_web_provider.py tests/test_zero_token_flow.py
git commit -m "feat(zero-token): inject context reminder into continuation and existing-conv messages"
```

---

### Task 4: 修复旧测试适配新行为

**文件:**

- 修改: `tests/test_zero_token_flow.py`

**Step 1: 更新断言**

`test_continuation_sends_only_new_tool_results` 中的 `assert "Sys." not in prompt` 需要更新——continuation 现在包含上下文提醒：

```python
def test_continuation_sends_only_new_tool_results(self):
    messages = [
        {"role": "system", "content": "Sys."},
        {"role": "user", "content": "Do stuff"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "t1", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "t2", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "a", "name": "t1", "content": "result_a"},
        {"role": "tool", "tool_call_id": "b", "name": "t2", "content": "result_b"},
    ]
    prompt, _ = self.provider._convert_continuation(messages)
    assert "result_a" in prompt
    assert "result_b" in prompt
    assert "Please proceed" in prompt
    # _convert_continuation 本身不注入提醒（由 _convert_messages 层处理）
    # 所以这里的断言保持不变
```

注意：`_convert_continuation` 本身不改，上下文提醒在 `_convert_messages` 层注入。所以这个测试应该不需要改。如果其他测试因为新增的 `session_key` 参数而断言失败，按需调整。

**Step 2: 运行全部测试**

运行: `uv run pytest tests/test_zero_token_flow.py tests/test_xml_tool_parser.py -v`
预期: 全部 PASS

**Step 3: 提交**

```bash
git add tests/test_zero_token_flow.py
git commit -m "test(zero-token): update tests for context reminder injection"
```

---

### Task 5: 清理 clear_session 和轮转逻辑

**文件:**

- 修改: `nanobot/providers/claude_web_provider.py`

**Step 1: 更新 `clear_session` 同时清理 `_system_prompts`**

```python
def clear_session(self, key: str | None = None) -> None:
    """Clear conversation mapping for a session (or all if key is None)."""
    if key is None:
        self._conversations.clear()
        self._turn_counts.clear()
        self._system_prompts.clear()
        logger.info("[zero-token] cleared all conversation mappings")
    elif key in self._conversations:
        del self._conversations[key]
        self._turn_counts.pop(key, None)
        self._system_prompts.pop(key, None)
        logger.info("[zero-token] cleared conversation for session {}", key)
    self._save_conversations()
```

**Step 2: 会话轮转时重新缓存系统提示**

```python
if conv_id is not None and turn_count >= self._CONV_ROTATION_TURNS:
    logger.info(
        "[zero-token] rotating conversation for session {} after {} turns",
        session_key, turn_count,
    )
    self._conversations.pop(session_key, None)
    self._turn_counts[session_key] = 0
    conv_id = None
    # 重新缓存系统提示（可能因为 memory 更新而变化）
    for msg in messages:
        if msg.get("role") == "system":
            self._system_prompts[session_key] = msg.get("content", "")
            break
```

**Step 3: 运行测试**

运行: `uv run pytest tests/test_zero_token_flow.py -v`
预期: 全部 PASS

**Step 4: 提交**

```bash
git add nanobot/providers/claude_web_provider.py
git commit -m "fix(zero-token): clear cached prompts on session clear, refresh on rotation"
```

---

## 变更汇总

| 变更                            | 效果                                              |
| ------------------------------- | ------------------------------------------------- |
| `_CONV_ROTATION_TURNS`: 20 → 12 | 更频繁地轮转会话，重新注入完整上下文              |
| `_system_prompts` 缓存          | 跨轮次记住系统提示用于构建提醒                    |
| `_build_context_reminder()`     | 每次非 full_prompt 轮次注入精简身份 + 规则 + 工具 |
| 深度刷新（每 5 轮）             | 完整系统提示重新注入，避免昂贵的会话轮转          |
| Continuation 注入上下文提醒     | 工具结果消息现在包含身份上下文                    |
| 已有会话注入上下文提醒          | 新用户消息包含身份上下文                          |

## 验证

全部任务完成后：

1. `uv run pytest tests/test_zero_token_flow.py tests/test_xml_tool_parser.py -v` — 全绿
2. 手动测试：启动 zero-token 会话，进行 10+ 轮含工具调用的对话，验证模型保持身份认知并记住早期上下文

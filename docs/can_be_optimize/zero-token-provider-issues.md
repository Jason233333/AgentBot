# Zero-Token Provider 问题清单

> 版本：基于 nanobot 0.1.4.post4
> 涉及文件：
>
> - `nanobot/providers/claude_web_provider.py`
> - `nanobot/providers/claude_web_client.py`
> - `nanobot/agent/loop.py`
> - `nanobot/providers/xml_tool_parser.py`

---

## P0: `/new` 命令不会清理 Web 端会话

### 问题

`ClaudeWebProvider.clear_conversations()` 已定义但**从未被调用**。

当用户执行 `/new` 时，`AgentLoop._process_message()`（loop.py:404）会清空 nanobot session，但 provider 内部的 `_conversations` dict（session_key → claude.ai conversation_id 的映射）不会被清理。

由于 `_get_session_key()` 基于 system prompt hash，而 system prompt 在 `/new` 前后不变，同一个 session_key 会映射到旧的 conversation_id。结果：

- 用户以为开了新对话
- 实际上消息仍然发到 claude.ai 上的旧 conversation

### 影响范围

所有使用 `zero` 或 `normal-zero` 模式的用户。

### 修复方向

在 `AgentLoop` 处理 `/new` 命令时，调用 `provider.clear_conversations()`。需要注意 `FallbackProvider` 场景（需要递归清理 secondary）。

```python
# loop.py — /new 处理逻辑中增加
if hasattr(self.provider, 'clear_conversations'):
    self.provider.clear_conversations()
```

---

## P1: SSE 解析忽略错误事件，失败时静默返回空字符串

### 问题

`claude_web_client.py` 的 `send_message()` 中 SSE 解析只处理了两种事件类型：

```javascript
if (data.type === 'completion' && data.completion) {
    fullText += data.completion;
} else if (data.type === 'content_block_delta' && data.delta && data.delta.text) {
    fullText += data.delta.text;
}
```

缺少对以下事件的处理：

- **`error`** — claude.ai 返回的错误（rate limit、overload、account limit）
- **`message_limit`** — 用户用量超限
- thinking blocks（`delta.type === "thinking_delta"`）

当 claude.ai 返回错误事件时，`fullText` 始终为空字符串，`send_message()` 返回 `""`，上层 `_parse_response("")` 返回 `LLMResponse(content="", finish_reason="stop")`。

### 影响

agent loop 认为请求成功完成，但实际给用户返回空回复，且没有任何日志可供排查。

### 修复方向

在 SSE 解析中捕获 error 类型事件，将其作为异常抛出或写入返回值：

```javascript
if (data.type === 'error') {
    throw new Error(`Claude Web error: ${JSON.stringify(data.error || data)}`);
}
```

---

## P1: Conversation 失效时 continuation 路径丢失全部上下文

### 问题

`_convert_messages()` 通过检测 `role == "tool"` 来判断是否走 continuation 路径：

```python
has_tool_results = any(m.get("role") == "tool" for m in messages)
if has_tool_results:
    return self._convert_continuation(messages)
```

Continuation 路径只发送最后一轮的 tool results，**依赖 claude.ai 同一个 conversation 保持完整上下文**。

如果 conversation 因以下原因失效：
- claude.ai 端自动清理了对话
- 网络断开重连后 conversation 被重建
- 任何导致 `_conversations` dict 被清空的场景

后续轮次会在一个**全新的 conversation 里只发 tool results**，没有 system prompt、没有用户原始消息、没有工具定义。Claude 完全不知道上下文是什么。

### 影响

长时间运行的 agent 在多轮 tool 调用中可能突然"失忆"，给出无意义的回复。

### 修复方向

1. 在 conversation 失效时自动回退到首轮完整 prompt 路径
2. 或者在 `send_message` 返回空/异常时重建 conversation 并重发完整消息

---

## P2: `_map_model()` prefix 剥离逻辑不正确

### 问题

```python
model_lower = model.lower().replace("-", "").replace("_", "")
# ...
for prefix in ("anthropic/", "claude_web/"):
    if model.lower().startswith(prefix):
        clean = model_lower[len(prefix.replace("/", "")):]
        break
```

步骤拆解（以 `"anthropic/claude-sonnet-4-6"` 为例）：

1. `model_lower` = `"anthropic/claudesonnet46"`（去掉了 `-` 和 `_`，但保留了 `/`）
2. `model.lower().startswith("anthropic/")` → True
3. `prefix.replace("/", "")` = `"anthropic"`，长度 9
4. `clean = "anthropic/claudesonnet46"[9:]` = `"/claudesonnet46"`

`clean` 带了一个多余的 `/` 前缀。后续 `if key in clean` 匹配时，`"claudesonnet46" in "/claudesonnet46"` 碰巧为 True，所以**目前能正常工作**。

### 影响

当前无实际影响，但逻辑有误。如果后续添加的 mapping key 恰好与 `/` 前缀产生冲突，会导致模型映射失败。

### 修复方向

prefix 剥离时也要去掉 `/`：

```python
for prefix in ("anthropic/", "claude_web/"):
    if model.lower().startswith(prefix):
        clean = model_lower[len(prefix):].lstrip("/")
        break
```

或者更简单：直接在 `model_lower` 构造时也 replace 掉 `/`。

---

## P3: content 为 None 时序列化为 "null"

### 问题

`_convert_messages()` 和 `_convert_continuation()` 中：

```python
result = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
```

当 `content` 为 `None` 时（比如 assistant 消息只有 tool_calls 没有文本），`isinstance(None, str)` 为 False，走 `json.dumps(None)` 返回字符串 `"null"`。

### 影响

不影响功能，但 Claude Web 端会看到一个文本 `"null"` 作为 tool result，可能造成轻微混淆。

### 修复方向

```python
result = content if isinstance(content, str) else (json.dumps(content, ensure_ascii=False) if content is not None else "")
```

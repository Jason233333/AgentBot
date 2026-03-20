# Zero-Token 集成：Claude Web 自动化

> **分支**: `feat/zero-token`
> **创建时间**: 2026-03-18
> **状态**: 已完成

## 概述

通过 Playwright 浏览器自动化与 claude.ai Web 界面交互，替代付费 API 调用。支持三种运行模式：

| 模式          | 行为                               |
| ------------- | ---------------------------------- |
| `normal`      | 仅 API（默认）                     |
| `zero`        | 仅 Web（零 token 消耗）            |
| `normal-zero` | 优先 API，单次调用失败时降级到 Web |

## 架构决策

### Session 始终存储 JSON

- Session 历史始终使用标准 OpenAI 格式的 JSON
- Provider 层负责双向转换（JSON ↔ XML）
- 切换模式对 Session 管理完全透明

### Agent Loop 零改动

- 新 provider 实现 `LLMProvider` 接口
- `FallbackProvider` 组合 primary + secondary，按单次调用降级（非会话级）
- Agent loop 只看到标准 `LLMResponse`

### 转换发生在 Provider 边界

```
Session (JSON) ──→ ClaudeWebProvider._convert_messages() ──→ Web (XML prompt)
                         ↕ 转换层
Session (JSON) ←── ClaudeWebProvider._parse_response()    ←── Web (纯文本 + XML 标签)
```

## 文件变更

### 新增文件（5 个）

| 文件                                       | 行数 | 职责                                                  |
| ------------------------------------------ | ---- | ----------------------------------------------------- |
| `nanobot/providers/xml_tool_parser.py`     | ~100 | 工具定义 ↔ XML 文本，tool_call XML ↔ ToolCallRequest  |
| `nanobot/providers/claude_web_client.py`   | ~250 | Playwright CDP 连接、cookie、page.evaluate fetch、SSE |
| `nanobot/providers/claude_web_provider.py` | ~300 | LLMProvider 实现：消息转换、响应解析                  |
| `nanobot/providers/claude_web_auth.py`     | ~120 | Chrome 登录流程、cookie 持久化                        |
| `nanobot/providers/fallback_provider.py`   | ~80  | FallbackProvider：primary + secondary 按次降级        |

### 修改文件（3 个）

| 文件                       | 改动内容                                                 |
| -------------------------- | -------------------------------------------------------- |
| `nanobot/config/schema.py` | 新增 `ClaudeWebConfig`，`AgentDefaults` 增加 `mode` 字段 |
| `nanobot/cli/commands.py`  | `_make_provider()` 支持 3 种模式，新增 `claude-web` 登录 |
| `pyproject.toml`           | 新增 `zero-token` 可选依赖组                             |

## 数据流

### Normal 模式

```
agent loop → LiteLLMProvider.chat(messages, tools) → API → LLMResponse
```

### Zero 模式

```
agent loop → ClaudeWebProvider.chat(messages, tools)
  → _convert_messages(): 将工具定义注入为 XML 文本，转换历史消息
  → ClaudeWebClient.send_message(): Playwright 在浏览器内 fetch claude.ai
  → _parse_response(): 正则提取 <tool_call> 标签 → ToolCallRequest
  → LLMResponse（标准 JSON 格式，直接存入 session）
```

### Normal-Zero 模式

```
agent loop → FallbackProvider.chat(messages, tools)
  → 尝试: primary.chat()（LiteLLM API）
  → 失败时: secondary.chat()（ClaudeWeb）
  → LLMResponse
```

## 关键实现细节

### XML 工具注入格式

工具定义通过显式指令 + few-shot 示例注入，确保模型稳定输出 `<tool_call>` XML 标签：

```
## Tool Use Instructions

You have access to external tools. You MUST use them when appropriate.
To call a tool, output the following XML tag — this is the ONLY way to invoke tools:

<tool_call id="unique_id" name="tool_name">{"param": "value"}</tool_call>

### Example

User: What's the weather in Tokyo?
Assistant: I'll check the weather for you.
<tool_call id="call_1" name="get_weather">{"city": "Tokyo"}</tool_call>

### Available Tools

#### read_file
Read a file
Parameters: {"type": "object", "properties": {"path": {"type": "string"}}}
```

### XML 工具调用解析

正则：`<tool_call\s+id="([^"]+)"\s+name="([^"]+)">([\s\S]*?)</tool_call>`

- 容忍 markdown ` ```json ``` ` 包裹
- id 缺失时自动生成短 ID

### 工具结果格式（回传给 Web）

```xml
<tool_response id="abc123" name="read_file">
{文件内容}
</tool_response>
```

### 对话管理

- 内部维护 `_conversations: dict[str, str]` 映射 session → conversation_id
- 首轮：创建新对话 + 发送完整历史聚合 prompt
- 续轮：复用 conversation_id + 仅发送增量内容（工具结果）

## 局限性

- 工具调用可靠性依赖 LLM 遵循 XML 格式输出
- claude.ai session cookie 会过期，需要定期重新登录
- 首版不支持流式输出
- 仅支持 Claude Web（暂不支持 DeepSeek/ChatGPT）

## 测试验证

1. 单元测试：xml_tool_parser 解析逻辑
2. 集成测试：消息格式转换（mock 浏览器层）
3. 端到端测试：真实浏览器完整链路

---

## 使用方法

### 安装依赖

```bash
pip install nanobot-ai[zero-token]
playwright install chromium
```

### 启动 Chrome 调试端口

必须从终端启动 Chrome 并指定 `--user-data-dir` 才能开启 CDP：

```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-debug-profile

# Linux
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug-profile
```

> **注意**：启动前必须完全退出 Chrome（macOS 上 Cmd+Q）。
> 不指定 `--user-data-dir` 时，Chrome 会静默忽略 `--remote-debugging-port` 参数。

### 登录 claude.ai

在打开的 Chrome 窗口中登录 claude.ai，然后运行：

```bash
nanobot provider login claude-web
```

如果浏览器已登录，凭证会被自动捕获。

### 配置模式

编辑 `~/.nanobot/config.json`：

```json
{
  "agents": {
    "defaults": {
      "model": "claude-sonnet-4-6",
      "mode": "normal-zero"
    }
  }
}
```

| 模式       | 配置值          | 行为                       |
| ---------- | --------------- | -------------------------- |
| 仅 API     | `"normal"`      | 默认，无需修改             |
| 仅 Web     | `"zero"`        | 所有请求走 Claude Web      |
| API → 降级 | `"normal-zero"` | 优先 API，失败时降级到 Web |

### 启动 Gateway

```bash
# normal-zero 模式（启动时会打印模式信息）
nanobot gateway

# 或单条消息
nanobot agent -m "Hello"
```

当 `mode: "normal-zero"` 时，启动会打印：

```
Mode: normal-zero (API → Claude Web fallback)
```

### 切换回 API 模式

将 `"mode"` 设为 `"normal"`（或直接删除该字段）即可恢复 API 模式。

---

## 运行端到端测试

E2E 测试脚本走真实浏览器完整链路，不使用 mock。

### 前置条件

1. Chrome 已以调试端口启动（见上文）
2. 已在浏览器中登录 claude.ai
3. 凭证已保存（通过 `nanobot provider login claude-web` 或自动捕获）

### 运行测试

```bash
# 需要绕过代理以连接本地 CDP
http_proxy="" https_proxy="" all_proxy="" \
  python scripts/test_zero_token_e2e.py

# 只运行某个测试
python scripts/test_zero_token_e2e.py --test chat   # 简单对话
python scripts/test_zero_token_e2e.py --test tool   # 工具调用（XML 格式）
python scripts/test_zero_token_e2e.py --test multi  # 多轮：工具调用 → 结果 → 总结

# 自定义 CDP 地址或模型
python scripts/test_zero_token_e2e.py --cdp http://127.0.0.1:9222 --model claude-sonnet-4-6
```

### 测试覆盖内容

| 测试    | 验证流程                                                 |
| ------- | -------------------------------------------------------- |
| `chat`  | 发送 "2+3" → 收到 "5"（验证浏览器 → API → SSE 完整链路） |
| `tool`  | 注入工具 schema → 模型输出 `<tool_call>` XML → 解析成功  |
| `multi` | 工具调用 → 回传工具结果 XML → 模型基于结果生成总结       |

### Mock 测试（无需浏览器）

```bash
pytest tests/test_xml_tool_parser.py tests/test_zero_token_flow.py -v
```

共 31 个测试，覆盖 XML 解析、消息转换、降级逻辑和错误处理。

---

## 实现日志

### 遇到的问题及修复

1. **CORS 拦截**：`api.claude.ai` 与页面所在的 `claude.ai` 不同源。
   改为使用同源的 `https://claude.ai/api/...` 端点。

2. **Payload 格式不匹配**：claude.ai 要求 `{"prompt": "...", "model": "..."}` 在顶层，
   而非嵌套在 `completion` 键下。通过 `page.on('request')` 拦截实际浏览器请求发现。

3. **代理干扰**：本地 `http_proxy` 环境变量导致 Playwright CDP 连接失败。
   在 `connect_over_cdp()` 期间临时清除代理环境变量。

4. **Chrome 调试端口不开放**：macOS 上必须指定 `--user-data-dir` 才能使
   `--remote-debugging-port` 生效。否则 Chrome 静默忽略该参数。

5. **工具调用可靠性**：模型在新对话中偶尔不遵循 XML 工具格式。
   在工具注入 prompt 中添加显式指令 + few-shot 示例后解决。

### 测试结果

- **Mock 测试**：31/31 通过
- **E2E 测试**：3/3 通过（简单对话、工具调用、多轮对话）

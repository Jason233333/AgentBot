# 网络故障恢复后 Agent 不继续处理任务

> 项目：nanobot (HKUDS/nanobot)
> 代码版本：0.1.4.post4: 65cbd7eb78672e226a8108c81da3ed8ce50ab192
> 涉及文件：
>
> - `nanobot/providers/base.py:77`（`_CHAT_RETRY_DELAYS = (1, 2, 4)`，仅 3 次重试）
> - `nanobot/providers/base.py:192-265`（`chat_with_retry()`，无长期恢复机制）
> - `nanobot/agent/loop.py:228-233`（`finish_reason == "error"` 时直接 break）
> - `nanobot/agent/loop.py:317-325`（`_dispatch()` 异常后返回 error 消息，任务结束）

---

## 问题描述

当网络连接中断时，agent 的 LLM 调用会失败。即使网络随后恢复，agent 也不会重新处理正在进行的任务。用户必须重新发送消息才能继续。

### 故障链路

```
用户发消息
  → _dispatch()
    → _process_message()
      → _run_agent_loop()
        → provider.chat_with_retry()
            ├─ 重试 3 次（1s → 2s → 4s，总计 7 秒）
            └─ 全部失败 → 返回 LLMResponse(finish_reason="error")
        ← _run_agent_loop 检测到 error，break 退出
      ← 返回 "Sorry, I encountered an error calling the AI model."
    ← 消息处理彻底结束
  ← 发送错误消息给用户

[网络恢复]

→ 无任何恢复动作。用户必须重新发送消息。
```

### 三层问题

**第一层：重试窗口太短**

`_CHAT_RETRY_DELAYS = (1, 2, 4)` 意味着只有 7 秒的重试窗口。网络中断通常持续数十秒甚至数分钟，7 秒远不够。

**第二层：错误后直接放弃**

`_run_agent_loop()` 中检测到 `finish_reason == "error"` 后直接 break，不区分"临时网络问题"和"永久性错误"（如模型不存在、API key 无效）：

```python
# loop.py:230-233
if response.finish_reason == "error":
    logger.error("LLM returned error: {}", (clean or "")[:200])
    final_content = clean or "Sorry, I encountered an error calling the AI model."
    break  # 直接退出，不再重试
```

**第三层：无任务恢复机制**

错误响应不会持久化到 session（防止 poison context），但也没有将"待重试任务"记录到任何地方。任务直接丢失。

### 影响

| 场景                       | 表现                                             |
| -------------------------- | ------------------------------------------------ |
| 短暂网络波动（>7s）        | 用户收到错误消息，需要手动重发                   |
| agent 正在多轮 tool 调用中 | 已执行的 tool 结果全部丢失，重发后从头开始       |
| 子 agent 执行中网络断开    | 子 agent 同样失败，且主 agent 收到 error 通知    |
| 批量消息排队处理时         | 第一条失败后，后续消息仍会尝试（但可能同样失败） |

### 当前设计的合理性

- 错误响应不持久化是合理的——防止 400 循环（代码注释 #1303）
- 有限重试避免无限等待也是合理的
- 但 7 秒窗口太短，且缺少恢复路径

---

## 改进建议

### 方案 A：延长重试 + 指数退避（改动最小，推荐）

```python
# base.py — 延长重试延迟
_CHAT_RETRY_DELAYS = (1, 2, 4, 8, 16, 30)  # 6 次重试，总计约 61 秒
```

进一步改进为带上限的指数退避：

```python
_CHAT_RETRY_DELAYS = (1, 2, 4, 8, 16, 30, 30, 30)  # 8 次，最长等 30s/次，总计约 121 秒
```

**优势**：改一行代码，覆盖大多数短暂网络中断
**局限**：仍然无法应对分钟级别的网络故障

### 方案 B：agent loop 层面重试

在 `_run_agent_loop()` 中区分临时错误和永久错误，临时错误时重试 LLM 调用而非 break：

```python
# loop.py — _run_agent_loop 中
if response.finish_reason == "error":
    if self.provider._is_transient_error(response.content):
        logger.warning("Transient LLM error, will retry: {}", response.content[:120])
        await asyncio.sleep(10)
        continue  # 重新进入 while 循环，再次调用 LLM
    else:
        logger.error("Non-transient LLM error: {}", response.content[:200])
        final_content = clean or "Sorry, I encountered an error calling the AI model."
        break
```

**优势**：不浪费已完成的 tool 调用结果，从断点继续
**需要**：设置 loop 层面的错误重试上限（如最多 5 次连续错误），防止无限循环

### 方案 C：待重试队列

将失败的任务放入重试队列，网络恢复后自动重新处理：

```python
# loop.py — _dispatch 中
except Exception:
    logger.exception("Error processing message for session {}", msg.session_key)
    if self._is_retryable(e):
        await asyncio.sleep(30)
        await self.bus.publish_inbound(msg)  # 重新入队
    else:
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content="Sorry, I encountered an error.",
        ))
```

**优势**：完全自动恢复，用户无需重发
**需要**：

- 消息加 retry_count 字段，防止无限重试
- 重试延迟策略（指数退避）
- 最大重试次数（如 3 次后放弃并通知用户）

### 方案 D：多轮 tool 调用断点恢复

在每轮 tool 调用完成后，将中间状态持久化：

```python
# 伪代码
checkpoint = {
    "messages": messages,
    "iteration": iteration,
    "tools_used": tools_used,
}
session.save_checkpoint(checkpoint)
```

LLM 调用失败时，下次重试可以从 checkpoint 恢复，而非从头开始。

**优势**：不浪费已执行的 tool 结果（尤其是耗时操作如 web_search、exec）
**代价**：增加持久化开销和恢复逻辑复杂度

### 推荐

1. **立即实施**：方案 A（延长重试到 ~60s）——一行代码改动
2. **短期实施**：方案 B（loop 层面区分临时/永久错误并重试）——保护已完成的 tool 结果
3. **中期实施**：方案 C（重试队列）——完全自动恢复
4. 方案 D 收益高但复杂度也高，视需求决定

---

## 关联问题

- `nanobot-subagent-no-timeout-no-status.md` — 子 agent 同样存在网络故障无法恢复的问题，且更严重（没有用户可以手动重发）
- `nanobot-global-processing-lock.md` — 网络故障期间全局锁被持有（重试等待中），阻塞所有其他消息处理

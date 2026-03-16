# Fix: 网络恢复后自动注入 resume 消息，agent 继续工作

> 修复版本：基于 nanobot 0.1.4.post4
> 涉及文件：
>
> - `nanobot/agent/loop.py`（核心改动）
> - `tests/test_task_cancel.py`（新增测试）

---

## 问题回顾

原始实现中，`_run_agent_loop` 遇到 `finish_reason="error"` 时无条件 `break`：

```python
# 改动前
if response.finish_reason == "error":
    logger.error("LLM returned error: {}", ...)
    final_content = clean or "Sorry, I encountered an error..."
    break  # ← 直接放弃，已完成的 tool 结果全部丢失
```

这意味着：
- agent 正在执行多轮 tool 调用（比如已经跑了 5 步），网络抖动一下，整个任务就中断了
- 用户必须重新发消息，agent 从头开始，之前的上下文全部丢失
- 即使 `chat_with_retry` 已经重试了 6 次（61 秒），最终失败后仍然直接放弃

详见 `nanobot-no-recovery-after-network-failure.md`。

---

## 改动说明

在 `_run_agent_loop` 的错误处理分支里，区分**瞬态错误**和**永久错误**：

```python
# 改动后
if response.finish_reason == "error":
    logger.error("LLM returned error: {}", ...)
    if self.provider._is_transient_error(response.content):
        # 瞬态错误：注入 resume 消息，保留上下文，继续迭代
        logger.info("Transient error — injecting resume prompt (iteration {}/{})", ...)
        messages.append(
            {"role": "user", "content": "网络出现了短暂故障，请继续你之前的工作。"}
        )
        continue   # ← 不 break，继续下一轮迭代
    # 永久错误（401、invalid_api_key 等）：正常 break
    final_content = clean or "Sorry, I encountered an error..."
    break
```

### 执行流程

```
用户消息
  └─ _run_agent_loop
       ├─ iteration 1: tool_call → 执行工具 → 结果追加到 messages
       ├─ iteration 2: tool_call → 执行工具 → 结果追加到 messages
       ├─ iteration 3: chat_with_retry 返回 503（重试 6 次后仍失败）
       │    └─ _is_transient_error("503 ...") == True
       │         └─ messages.append({"role": "user", "content": "请继续工作"})
       │              └─ continue → iteration 4
       └─ iteration 4: LLM 看到完整上下文 + resume 提示 → 继续工作
```

### 为什么注入 user 消息而不是 system 消息？

- `system` 消息只能出现在 messages 列表的第一位（OpenAI 规范），追加到中间会导致 400 错误
- `user` 消息是最自然的"继续"信号，LLM 会将其理解为用户的指令
- 消息内容故意简短（"网络出现了短暂故障，请继续你之前的工作。"），不干扰 agent 的推理链

### 与 `chat_with_retry` 的关系

两层防御，各司其职：

| 层级 | 位置 | 作用 |
|------|------|------|
| `chat_with_retry` | `providers/base.py` | 单次 LLM 调用的重试（最多 6 次，61 秒） |
| resume 注入 | `agent/loop.py` | 整个 agent 迭代循环的恢复（保留 tool 上下文） |

`chat_with_retry` 先跑，6 次全失败后返回 `finish_reason="error"`，然后 loop 层的 resume 逻辑接手，注入消息让 agent 在下一次迭代重新尝试。

### 边界情况

- **`max_iterations` 保护**：resume 注入会消耗一次迭代计数，不会无限循环。如果网络持续断开，最终会因为 `iteration >= max_iterations` 退出并提示用户。
- **非瞬态错误不受影响**：`401 Unauthorized`、`invalid_api_key` 等永久错误，`_is_transient_error` 返回 `False`，走原来的 `break` 路径，不会无意义重试。
- **resume 消息不写入 session**：`_save_turn` 只保存 `initial_messages` 之后的新消息，resume 注入的 user 消息在 `all_msgs` 里，但因为它是在 error 路径产生的，后续 LLM 的正常回复会覆盖这个位置，不会污染 session 历史。

---

## 测试覆盖

新增 `TestAgentLoopResume` 测试类（`tests/test_task_cancel.py`）：

| 测试 | 验证点 |
|------|--------|
| `test_transient_error_injects_resume_and_continues` | 503 错误后注入 resume 消息，第二次调用成功，返回正确结果 |
| `test_non_transient_error_breaks_immediately` | 非瞬态错误（401）只调用一次就 break，不注入 resume |

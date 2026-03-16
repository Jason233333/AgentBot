# Fix: Subagent 超时控制与并发上限

> 修复版本：基于 nanobot 0.1.4.post4
> 涉及文件：
>
> - `nanobot/agent/subagent.py`（核心改动）
> - `tests/test_task_cancel.py`（新增测试）

---

## 问题回顾

原始实现中 `_run_subagent()` 没有任何总执行时间限制，`spawn()` 也没有并发数量检查：

- 一个 hang 住的 LLM 调用会让 subagent 永久占用 asyncio task，直到进程重启
- 主 agent 可以无限 spawn，最终耗尽系统资源

详见 `nanobot-subagent-no-timeout-no-status.md`。

---

## 改动说明

### 1. 并发上限（`spawn()` 入口检查）

```python
# subagent.py
MAX_CONCURRENT = 10   # 类常量，可通过构造参数覆盖

async def spawn(self, task, ..., max_concurrent=MAX_CONCURRENT) -> str:
    running = len(self._running_tasks)
    if running >= self.max_concurrent:
        logger.warning(...)
        return "Cannot spawn subagent: the concurrency limit of N is already reached. ..."
```

- 检查在 `spawn()` 入口同步完成，无竞态（asyncio 单线程）
- 返回错误字符串而非抛异常，主 agent 可直接将消息转发给用户
- 默认上限 10，可通过 `SubagentManager(max_concurrent=N)` 覆盖

### 2. 总超时（`_run_subagent` 拆分为 wrapper + inner）

原来的 `_run_subagent` 被拆成两层：

```python
async def _run_subagent(self, task_id, task, label, origin) -> None:
    """外层：负责超时控制和错误通知。"""
    try:
        await asyncio.wait_for(
            self._run_subagent_inner(task_id, task, label, origin),
            timeout=self.timeout_s,   # 默认 300s
        )
    except asyncio.TimeoutError:
        logger.error("Subagent [{}] timed out after {}s", task_id, self.timeout_s)
        await self._announce_result(..., status="error")
    except asyncio.CancelledError:
        logger.info("Subagent [{}] was cancelled", task_id)
        raise  # 让 cancel_by_session 正常工作

async def _run_subagent_inner(self, task_id, task, label, origin) -> None:
    """内层：原有的 agent loop 逻辑，不变。"""
    ...
```

**为什么拆两层而不是直接在原函数加 `wait_for`？**

- `asyncio.TimeoutError` 需要在 `wait_for` 的调用方捕获，否则会向上传播到 `asyncio.create_task` 的 done callback，导致 `_cleanup` 不被调用
- 拆层后 `_run_subagent_inner` 可以被测试直接调用，不受超时影响

### 3. 构造参数

```python
SubagentManager(
    ...
    max_concurrent: int = 10,   # 并发上限
    timeout_s: int = 300,       # 单个 subagent 总超时（秒）
)
```

两个参数均有合理默认值，不影响现有调用方。

---

## 测试覆盖

新增 `TestSubagentConcurrencyAndTimeout` 测试类（`tests/test_task_cancel.py`）：

| 测试 | 验证点 |
|------|--------|
| `test_spawn_rejects_when_cap_reached` | 达到上限时 `spawn()` 返回错误字符串，不创建新 task |
| `test_subagent_timeout_announces_error` | 超时后调用 `_announce_result` 并传入 `status="error"` |

---

## 已知局限

- `timeout_s` 目前不可通过配置文件设置，需要代码层面传参。后续可在 `AgentDefaults` 或 `ToolsConfig` 中暴露配置项。
- 超时后已完成的 tool 结果不会被保存，subagent 的中间状态全部丢失。这是 `asyncio.wait_for` 的语义决定的，属于可接受的 tradeoff。

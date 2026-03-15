# 子 Agent 缺少超时控制与状态监控

> 项目：nanobot (HKUDS/nanobot)
> 代码版本：0.1.4.post4: 65cbd7eb78672e226a8108c81da3ed8ce50ab192
> 涉及文件：
>
> - `nanobot/agent/subagent.py:49-79`（spawn，无超时参数）
> - `nanobot/agent/subagent.py:81-164`（\_run_subagent，无总超时）
> - `nanobot/agent/subagent.py:220-228`（cancel_by_session，仅用户手动触发）
> - `nanobot/providers/base.py`（chat_with_retry，无总超时上限）

---

## 问题一：无子 Agent 总超时

### 现状

`_run_subagent()` 没有任何总执行时间限制。现有保护机制：

| 机制                  | 作用                | 局限                                   |
| --------------------- | ------------------- | -------------------------------------- |
| `max_iterations = 15` | 限制 LLM 调用轮数   | 只防工具调用无限循环，不防单次调用卡住 |
| `cancel_by_session()` | 用户发 `/stop` 取消 | 需要用户主动操作，无法自动触发         |
| `exec` 工具 timeout   | 单条命令 60s 超时   | 仅限 exec 工具，其他工具无超时         |

### 会 hang 住的场景

1. **LLM API 不返回**：`chat_with_retry()` 会以 1→2→4s 间隔重试，但没有总超时上限。如果 API 持续返回 5xx 或网络不通，子 agent 会无限重试
2. **工具执行阻塞**：`read_file`、`web_fetch`、`web_search` 等工具没有明确的超时设置，网络请求可能无限等待
3. **15 轮都是慢操作**：每轮 LLM 调用 + 工具执行如果都很慢（例如每轮 30s），15 轮就是 7.5 分钟，期间无法干预

### 影响

- hang 住的子 agent 永久占用内存和 asyncio Task 槽位
- `_running_tasks` 中的僵尸任务不断累积
- 用户无感知——主 agent 不会主动告知"子 agent 可能卡住了"
- 无法自动恢复，只能等用户手动 `/stop`

---

## 问题二：无中间状态上报

### 现状

子 agent 的状态对主 agent 完全不透明：

```
spawn() → "I'll notify you when it completes."
              ↓
         [黑盒，主 agent 完全不知道发生了什么]
              ↓
         _announce_result() → 完成/失败通知
```

主 agent 无法得知：

- 子 agent 当前执行到第几轮
- 子 agent 正在调用什么工具
- 子 agent 是否还活着
- 子 agent 预计还需要多久

### 影响

- 用户问"子 agent 怎么样了"，主 agent 无法回答
- 长时间无响应时，无法区分"正在努力工作"和"已经卡死"
- 无法做优先级调度（不知道哪个子 agent 快完成了）

---

## 问题三：子 Agent 数量无上限

### 现状

`spawn()` 没有检查当前运行中的子 agent 数量，理论上可以无限创建：

```python
# subagent.py:62-65
bg_task = asyncio.create_task(self._run_subagent(...))
self._running_tasks[task_id] = bg_task
# 没有任何数量检查
```

### 影响

- 主 agent 如果在循环中反复 spawn，会耗尽内存
- 多个子 agent 同时调用 LLM API，可能触发提供商速率限制
- 每个子 agent 都有独立的 messages 列表，内存线性增长

---

## 改进建议

### 方案 A：添加总超时（推荐，改动最小）

```python
# subagent.py — _run_subagent 加 asyncio.wait_for
async def _run_subagent(self, task_id, task, label, origin):
    try:
        await asyncio.wait_for(
            self._execute_task(task_id, task, label, origin),
            timeout=300,  # 5 分钟总超时
        )
    except asyncio.TimeoutError:
        logger.warning("Subagent [{}] timed out after 300s", task_id)
        await self._announce_result(task_id, label, task,
            "Error: task timed out after 5 minutes", origin, "error")
```

配套：在 `spawn()` 参数或配置中允许自定义超时时间。

### 方案 B：添加进度上报

在每轮迭代后向主 agent 发送轻量级状态更新：

```python
# 每轮迭代后
if iteration % 5 == 0:  # 每 5 轮汇报一次
    progress_msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id=f"{origin['channel']}:{origin['chat_id']}",
        content=f"[Subagent '{label}' progress: iteration {iteration}/{max_iterations}]",
    )
    await self.bus.publish_inbound(progress_msg)
```

或者提供一个查询接口，让主 agent 用工具主动查询：

```python
def get_status(self, task_id: str) -> dict:
    """返回子 agent 当前状态：running/completed/failed + 当前轮数"""
```

### 方案 C：添加并发上限

```python
MAX_CONCURRENT_SUBAGENTS = 5

async def spawn(self, task, ...):
    if len(self._running_tasks) >= MAX_CONCURRENT_SUBAGENTS:
        return "Error: too many subagents running. Wait for some to complete or use /stop."
    # ... 正常 spawn
```

### 推荐组合

1. **立即实施**：方案 A（总超时）+ 方案 C（并发上限）——改动小，防止资源泄漏
2. **后续优化**：方案 B（进度上报）——改善用户体验，但需要考虑进度消息对主 agent 上下文的干扰

---

## 关联问题

- `nanobot-global-processing-lock.md` — 子 agent 结果回注后仍需排队等全局锁，如果主 agent 正忙，通知会延迟
- `nanobot-memory-unbounded-growth.md` — 子 agent 的执行不写入 MEMORY.md，但如果主 agent 把结果存入记忆，会加剧记忆增长问题

# Fix: 全局锁 → 会话级锁 + 全局 Semaphore

> 修复版本：基于 nanobot 0.1.4.post4
> 涉及文件：
>
> - `nanobot/agent/loop.py`（核心改动）
> - `tests/test_task_cancel.py`（测试更新）

---

## 问题回顾

原始实现使用一把全局 `asyncio.Lock` 串行化所有消息处理：

```python
# 改动前 — loop.py
self._processing_lock = asyncio.Lock()

async def _dispatch(self, msg):
    async with self._processing_lock:   # 全局锁，所有 session 共用
        ...
```

这意味着：
- 不同 Discord 频道、不同 Telegram 用户的消息互相阻塞
- 一个慢 LLM 调用（或重试等待）会让所有其他用户排队
- 多 channel 部署场景下，并发能力为 1

详见 `nanobot-global-processing-lock.md`。

---

## 改动说明

### 数据结构

```python
# 改动后 — loop.py __init__
# 每个 session_key 独立一把锁，同 session 消息串行，不同 session 并发
self._session_locks: dict[str, asyncio.Lock] = {}

# 全局 Semaphore 防止并发数量无上限增长（默认 32）
self._concurrency_sem = asyncio.Semaphore(32)
```

### `_dispatch` 改动

```python
def _get_session_lock(self, session_key: str) -> asyncio.Lock:
    """懒创建 per-session 锁。asyncio 单线程，无竞态。"""
    if session_key not in self._session_locks:
        self._session_locks[session_key] = asyncio.Lock()
    return self._session_locks[session_key]

async def _dispatch(self, msg: InboundMessage) -> None:
    session_lock = self._get_session_lock(msg.session_key)
    async with session_lock:           # 同 session 串行
        async with self._concurrency_sem:  # 全局并发上限
            ...  # 原有处理逻辑不变
```

### 并发语义

| 场景 | 改动前 | 改动后 |
|------|--------|--------|
| 同一用户连续发两条消息 | 串行（正确） | 串行（正确，保持） |
| 两个不同用户同时发消息 | 串行（错误） | 并发（正确） |
| 100 个用户同时发消息 | 串行（错误） | 最多 32 个并发（受控） |

### 为什么保留 Semaphore？

不加 Semaphore 的纯 per-session 锁在极端情况下（大量不同 session 同时活跃）会导致无限并发，耗尽 LLM API 配额或内存。32 是一个保守的默认值，可根据实际 API 限额调整。

### `_session_locks` 的内存泄漏问题

当前实现中 `_session_locks` 只增不减。对于长期运行的实例，如果 session 数量很大，会有轻微内存泄漏（每个 Lock 对象约 200 bytes）。

**短期可接受**：10,000 个 session 约 2MB，影响可忽略。
**中期改进方向**：在 session 过期时同步清理对应的 lock，或使用 `weakref.WeakValueDictionary`。

---

## 测试覆盖

`tests/test_task_cancel.py` 的改动：

1. **`test_processing_lock_serializes`** → 重命名为 **`test_same_session_serializes`**
   - 语义不变：同 session 的两条消息必须串行处理

2. **`test_different_sessions_run_concurrently`**（新增）
   - 两条来自不同 `chat_id` 的消息并发 dispatch
   - 验证两者都在对方结束前开始处理（即真正并发）

---

## 与其他改动的关系

- **依赖 `fix-retry-delays.md`**：重试等待期间 per-session 锁被持有，同 session 后续消息等待。这是正确行为，不是 bug。
- **被 `fix-subagent-timeout-and-concurrency-cap.md` 补充**：subagent 在 session 锁外运行（通过 `asyncio.create_task`），不受 per-session 锁影响，需要独立的并发控制。

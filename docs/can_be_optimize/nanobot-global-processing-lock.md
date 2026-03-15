# 全局处理锁导致并发瓶颈

> 项目：nanobot (HKUDS/nanobot)
> 代码版本：0.1.4.post4: 65cbd7eb78672e226a8108c81da3ed8ce50ab192
> 涉及文件：
>
> - `nanobot/agent/loop.py:102`（锁定义）
> - `nanobot/agent/loop.py:250-270`（\_dispatch 获取锁）

---

## 问题描述

AgentLoop 使用一把全局 `asyncio.Lock` 串行化所有消息处理：

```python
# loop.py:102
self._processing_lock = asyncio.Lock()
```

每条入站消息在 `_dispatch()` 中必须获取这把锁，**不区分来源频道和会话**。这意味着：

- 同一时刻只有一条消息在被 LLM 处理
- 不同 Discord 频道 / Slack workspace / Telegram 群组的消息也互相阻塞
- 后到的消息在 asyncio.Queue 中排队等待

### 影响

| 场景             | 表现                                                   |
| ---------------- | ------------------------------------------------------ |
| 单用户单频道     | 无影响，本身就是串行对话                               |
| 多频道同时活跃   | 频道 B 的用户必须等频道 A 的 LLM 调用完成才能得到响应  |
| 高峰期           | 响应延迟线性叠加：N 条排队消息 × 单次处理耗时          |
| 长时间 tool 调用 | 一个会话执行耗时工具（如大文件读写）会阻塞所有其他会话 |

实际吞吐量 ≈ **1 / 单次处理耗时**。若单次 LLM 调用含多轮 tool 执行耗时 10 秒，全局吞吐量仅 ~6 条/分钟。

### 当前设计的合理性

这把全局锁并非无意义——它简化了以下问题：

- 避免多个请求同时读写同一个 Session 的竞态条件
- 避免 MEMORY.md 被并发写入导致损坏
- 避免 consolidation 和正常处理交叉执行

---

## 改进建议

### 方案 A：会话级锁（推荐）

将全局锁改为 per-session_key 锁，不同会话可以并行处理：

```python
# 伪代码
self._session_locks: dict[str, asyncio.Lock] = {}

async def _dispatch(self, msg: InboundMessage):
    lock = self._session_locks.setdefault(msg.session_key, asyncio.Lock())
    async with lock:
        await self._process(msg)
```

**优势**：不同频道/用户的消息可并行处理，同一会话内仍保持串行
**需要解决**：

- MEMORY.md 并发写入保护（需单独加文件锁或用 `asyncio.Lock` 保护写入操作）
- Session JSONL 文件的并发安全（每个 session 已经是独立文件，天然隔离）
- LLM API 并发请求的速率限制管理

### 方案 B：Worker Pool

使用固定大小的 worker pool 限制并发度：

```python
self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_SESSIONS)  # 例如 3

async def _dispatch(self, msg: InboundMessage):
    async with self._semaphore:
        session_lock = self._get_session_lock(msg.session_key)
        async with session_lock:
            await self._process(msg)
```

**优势**：控制资源消耗上限，防止 LLM API 过载
**代价**：需要调优 MAX_CONCURRENT_SESSIONS 参数

### 方案 C：多实例部署

不改代码，通过运维解决：

- 每个频道/用途部署独立的 nanobot 实例
- 用不同的 bot token 和工作目录
- 天然隔离，互不影响

**优势**：零代码改动
**代价**：运维复杂度增加；无法共享 MEMORY.md（可能是优势也可能是劣势）

### 推荐

短期采用 **方案 C**（多实例）满足隔离需求。中期实现 **方案 A + B 结合**（会话级锁 + 信号量上限），兼顾并发能力和资源控制。

---

## 关联

- 全局锁也影响 subagent 结果回注——subagent 完成后的 `channel="system"` 消息同样需要排队等锁
- consolidation 期间会话被锁定，其他会话不受影响（方案 A 下）；当前全局锁下所有会话被阻塞

# nanobot 优化路线图

> 基于 nanobot 0.1.4.post4 源码分析整理。
> 详细分析见 `docs/can_be_optimize/` 和 `docs/tutorials/` 下的各专题文档。

---

## P0：可靠性

### 1. 网络故障后任务丢失 ✅ 已修复（部分）

**问题**：LLM 调用仅重试 3 次（共 7 秒），失败后直接放弃，已完成的 tool 结果全部丢失，用户需手动重发。

**动机**：网络波动是生产环境常态，7 秒窗口远不够。agent 正在多轮 tool 调用时断网，损失最大。

**已完成**：
1. 重试延迟序列从 `(1, 2, 4)` 延长为 `(1, 2, 4, 8, 16, 30)`，总窗口 61 秒。
2. agent loop 层面区分瞬态/永久错误：瞬态错误注入 `"请继续工作"` user 消息并 `continue`，保留已完成的 tool 上下文。

📄 `nanobot-no-recovery-after-network-failure.md` | 📄 `fix-retry-delays.md` | 📄 `fix-transient-error-resume.md`

### 2. 子 Agent 无超时、无状态监控 ✅ 已修复（部分）

**问题**：子 agent 没有总执行时间限制，hang 住后永久占用资源。主 agent 无法查询子 agent 进度。并发数量也无上限。

**动机**：LLM API 不返回或工具阻塞时，僵尸任务会累积，用户完全无感知。

**已完成**：`asyncio.wait_for` 加总超时（默认 300s）；`spawn()` 入口加并发上限检查（默认 10）。

**待完成**：进度查询接口（主 agent 可查询 subagent 当前状态）。

📄 `nanobot-subagent-no-timeout-no-status.md` | 📄 `fix-subagent-timeout-and-concurrency-cap.md`

---

## P1：资源治理

### 3. MEMORY.md 无界增长

**问题**：长期记忆文件没有硬上限，完全依赖 LLM "自觉"控制大小，长期运行后会膨胀并挤占 context window。

**动机**：MEMORY.md 每次请求都注入 system prompt，过大会降低可用上下文、增加 token 成本。

**思路**：写入前检查大小，超阈值自动压缩或归档旧条目；prompt 层加强大小约束指引。

📄 `nanobot-memory-unbounded-growth.md`

### 4. 全局处理锁导致并发瓶颈 ✅ 已修复

**问题**：所有频道/会话共享一把 `asyncio.Lock`，同一时刻只处理一条消息，不同频道的用户互相阻塞。

**动机**：多频道部署时体验差——频道 B 必须等频道 A 的 LLM 调用完成才能响应。网络重试期间锁被持有，所有会话停摆。

**已完成**：改为 per-session_key 锁 + 全局 Semaphore（默认 32）控制并发上限。不同 session 并发处理，同 session 保持串行。

📄 `nanobot-global-processing-lock.md` | 📄 `fix-session-level-lock.md`

---

## P2：可扩展性

### 5. 单实例仅支持单个同类 Bot

**问题**：配置中每种频道只能填一个 bot token，多 bot 需要多进程部署。

**动机**：多人格 bot、多租户、A/B 测试等场景需要多个同类 bot，多进程部署运维成本高。

**思路**：Channel 配置从单对象改为命名字典；session_key 扩展为含 bot 名；出站路由区分目标 bot。

📄 `nanobot-single-bot-per-instance.md`

### 6. Agent 间无法通信

**问题**：不同 nanobot 实例之间没有通信机制。平台层显式过滤 bot 消息，subagent 也没有 message/spawn 工具。

**动机**：无法实现多 agent 协作（研究 agent → 写作 agent）、任务分发、专业化分工。

**思路**：单进程内建 Agent 总线（注册表 + 路由 + 循环检测）；跨进程则通过外部消息队列。

📄 `nanobot-no-inter-agent-communication.md`

---

## 依赖关系与推进顺序

```
P0（可靠性）→ P1（资源治理）→ P2（可扩展性）

P0-1 网络恢复 ──┐
                 ├─→ P1-4 会话级锁 ──→ P2-5 多 Bot
P0-2 子Agent超时 ┘         │            P2-6 Agent通信
                    P1-3 Memory治理（独立）
```

- P0 两项互相独立，可并行，应最先修复
- P1-4 依赖 P0-1（重试期间不应阻塞其他会话）
- P1-3 独立，随时可做
- P2 依赖 P1-4（多 bot / 多 agent 需要并发处理能力）

# Fix: 延长 LLM 重试窗口

> 修复版本：基于 nanobot 0.1.4.post4
> 涉及文件：
>
> - `nanobot/providers/base.py`（核心改动）
> - `tests/test_provider_retry.py`（测试更新）

---

## 问题回顾

原始实现的重试延迟序列为 `(1, 2, 4)`，总等待时间仅 7 秒，在生产环境中几乎等于没有重试：

```python
# 改动前
_CHAT_RETRY_DELAYS = (1, 2, 4)
```

网络抖动、LLM 服务过载（429/503）通常需要数十秒才能恢复，7 秒窗口远不够。

详见 `nanobot-no-recovery-after-network-failure.md`。

---

## 改动说明

```python
# 改动后
_CHAT_RETRY_DELAYS = (1, 2, 4, 8, 16, 30)
```

| 指标 | 改动前 | 改动后 |
|------|--------|--------|
| 重试次数 | 3 | 6 |
| 最大等待时间 | 7s | 61s |
| 最长单次间隔 | 4s | 30s |

**选择依据：**
- 指数退避（1→2→4→8→16）覆盖短暂抖动
- 最后一档 30s 覆盖 LLM 服务过载恢复场景（OpenAI/Anthropic 的 429 通常在 20-30s 内恢复）
- 总窗口 61s 对用户体验影响可接受（相比任务彻底失败）

---

## 测试覆盖

`tests/test_provider_retry.py` 的改动：

1. **`test_chat_with_retry_returns_final_error_after_retries`**（已有测试，更新）
   - 原测试硬编码了 3 次重试的 response 列表，改为动态读取 `LLMProvider._CHAT_RETRY_DELAYS` 长度，不再与具体数值耦合

2. **`test_chat_with_retry_full_delay_sequence`**（新增）
   - 验证完整的 delay 序列 `[1, 2, 4, 8, 16, 30]` 被正确触发
   - 验证总调用次数 = `len(_CHAT_RETRY_DELAYS) + 1`（最终兜底调用）

---

## 已知局限

- 重试期间持有 per-session 锁（见 `fix-session-level-lock.md`），同一 session 的后续消息会等待。这是正确行为——乱序处理比等待更糟糕。
- 重试延迟目前不可通过配置文件覆盖。如需针对特定环境调整，需修改 `_CHAT_RETRY_DELAYS` 类变量。

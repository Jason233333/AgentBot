# Upstream Merge: HKUDS/nanobot → jason_dev

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把 upstream HKUDS/nanobot main 分支的 210 个新 commit 合并进 jason_dev，保留 jason_dev 的 zero-token 功能。

**Architecture:** 执行 `git merge upstream/main`，逐文件手动解决冲突。冲突原则：upstream 是基础功能演进，jason_dev 是在其上的 zero-token 扩展，**两边改动都要保留**。

**Tech Stack:** Python, asyncio, nanobot, playwright (zero-token)

**Merge Base commit:** `61f0923`（origin/main 最新点，也是 jason_dev 和 upstream/main 的公共祖先）

---

## 冲突文件一览

| 文件                        | 风险  | jason_dev 改动                                    | upstream 改动                                |
| --------------------------- | ----- | ------------------------------------------------- | -------------------------------------------- |
| `nanobot/agent/loop.py`     | 🔴 高 | per-session lock, session_key 传递, resume prompt | timezone, CommandRouter, concurrency env var |
| `nanobot/cli/commands.py`   | 🔴 高 | ClaudeWeb provider 入口拆分                       | StreamRenderer/ThinkingSpinner, onboard 重构 |
| `nanobot/agent/subagent.py` | 🟡 中 | 并发上限, timeout, session_key, clear_session     | builtin skills 读取权限, prompt 安全警告     |
| `nanobot/config/schema.py`  | 🟡 中 | ClaudeWebConfig, mode 字段                        | 字段重组                                     |
| `nanobot/providers/base.py` | 🟢 低 | recovered_from_error, retry delay, \*\*kwargs     | extra_content, \_strip_image_content         |
| `pyproject.toml`            | 🟢 低 | zero-token extra (playwright)                     | 版本升级, litellm→anthropic, weixin extra    |
| `README.md`                 | 🟢 低 | jason-dev 章节                                    | 上游文档更新                                 |
| `.gitignore`                | 🟢 低 | 新增忽略规则                                      | 上游新增规则                                 |

---

## 合并原则

### loop.py 冲突决策

- **per-session lock**：两边都实现了，但 jason_dev 用硬编码 `asyncio.Semaphore(32)`，upstream 用环境变量 `NANOBOT_MAX_CONCURRENT_REQUESTS`（默认3）。**采用 upstream 的环境变量方式**（更灵活），但变量名保持 upstream 的 `_concurrency_gate`。
- **session_key 传递**：保留 jason_dev 的 `chat_kwargs` 传递方式。
- **resume prompt**：保留 jason_dev 的网络恢复注入逻辑。
- **timezone / CommandRouter / builtin skills**：完整保留 upstream 新增内容。

### commands.py 冲突决策

- 保留 jason_dev 的 `_make_claude_web_provider` + `_make_api_provider` 拆分。
- 完整保留 upstream 的 StreamRenderer、ThinkingSpinner、onboard 重构。

### subagent.py 冲突决策

- 保留 jason_dev 的并发上限、timeout、session_key、clear_session 全部逻辑。
- 追加 upstream 的 builtin skills 读取权限和 prompt 安全警告。

### schema.py 冲突决策

- 保留 jason_dev 新增的 `ClaudeWebConfig` 和 `mode` 字段。
- 接受 upstream 的字段重组。

### base.py 冲突决策

- 两边改动区域基本不重叠，Git 大概率自动合并，若有冲突两边都保留。

### pyproject.toml 冲突决策

- 保留 jason_dev 的 `zero-token` extra。
- 接受 upstream 的版本升级和 litellm→anthropic 替换（注意检查代码是否有 litellm import）。

---

## 执行步骤

### Task 1: 写计划文档 ✅

文件：`docs/plans/2026-03-26-upstream-merge.md`

### Task 2: 执行 merge 并解决冲突

```bash
git checkout jason_dev
git merge upstream/main
# 逐文件解决冲突
git add <resolved-files>
git merge --continue
```

**解决顺序（从低风险到高风险）：**

1. `.gitignore`
2. `README.md`
3. `pyproject.toml`
4. `nanobot/providers/base.py`
5. `nanobot/config/schema.py`
6. `nanobot/agent/subagent.py`
7. `nanobot/cli/commands.py`
8. `nanobot/agent/loop.py`

### Task 3: 验证合并结果

```bash
uv run pytest tests/ -x -q
uv run ruff check nanobot/
```

检查点：

- [ ] zero-token provider 相关测试通过
- [ ] xml tool parser 测试通过
- [ ] 无 litellm import 报错（upstream 已移除 litellm）

---

## 进度追踪

| 任务                | 状态    | 备注                                                |
| ------------------- | ------- | --------------------------------------------------- |
| 写计划文档          | ✅ 完成 |                                                     |
| 执行 merge          | ✅ 完成 | commit 725742f                                      |
| 解决 .gitignore     | ✅ 完成 | 两边规则合并                                        |
| 解决 README.md      | ✅ 完成 | 自动合并                                            |
| 解决 pyproject.toml | ✅ 完成 | 自动合并                                            |
| 解决 base.py        | ✅ 完成 | `_safe_chat` + `**kwargs`                           |
| 解决 schema.py      | ✅ 完成 | `ClaudeWebConfig` + `exclude=True`                  |
| 解决 subagent.py    | ✅ 完成 | 自动合并                                            |
| 解决 commands.py    | ✅ 完成 | 保留 `_make_claude_web_provider`，采用 backend 路由 |
| 解决 loop.py        | ✅ 完成 | 合并 session_key + streaming + concurrency_gate     |
| 验证测试            | ✅ 完成 | 628 passed, commit 686e1e0                          |

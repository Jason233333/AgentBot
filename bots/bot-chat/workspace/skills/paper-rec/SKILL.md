---
name: paper-rec
description: 每日论文推荐与深度讲解系统。定时检索 arXiv/HuggingFace 优质论文，生成阅读报告，发布到微信公众号。触发词："论文推荐"、"今日论文"、"paper recommendation"、"执行论文推荐任务"、"论文推送"。
---

# Paper Recommender — 论文推荐与讲解系统

## 何时使用

- 定时任务自动触发（每天北京时间 7:00）
- 用户手动请求："帮我推荐论文"、"今天有什么好论文"
- 用户要求执行论文推荐任务

## 工作流

### 自动模式（Cron 触发）

```bash
cd /Users/shing/Documents/Project/AgentBot/bots/bot-chat/workspace
python3 skills/paper-rec/scripts/paper_recommender.py --auto
```

### 手动模式

```bash
# 只检索，不发布
python3 skills/paper-rec/scripts/paper_recommender.py --search-only

# 检索 + 生成文章（不发布）
python3 skills/paper-rec/scripts/paper_recommender.py --dry-run

# 完整流程：检索 + 生成 + 发布
python3 skills/paper-rec/scripts/paper_recommender.py --auto

# 指定论文 ID 生成讲解
python3 skills/paper-rec/scripts/paper_recommender.py --paper-id 2603.12345
```

## 类别轮换

- 奇数天（1, 3, 5...）：SWE 方向
- 偶数天（2, 4, 6...）：RL / Agent / RLHF 方向

## 依赖

- Python 3.12+
- requests, feedparser (pip install)
- wechat-article-publisher skill（发布用）

## 配置

编辑 `config.json` 可调整：关键词、评分权重、数据源参数。

## 数据文件

- `data/papers.json`：论文追踪表
- `data/published_ids.txt`：已发布论文 ID（快速查重）

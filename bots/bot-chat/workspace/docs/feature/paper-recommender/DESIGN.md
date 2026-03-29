# Paper Recommender — 论文推荐与讲解系统

## 1. 需求概述

每天早上 7:00（北京时间）自动运行，检索优质论文，生成深度讲解文章，推送到微信公众号。

**核心约束**：
- 每天推送 **1 篇**精读文章
- 深度讲解风格：大白话 + 阅读报告（摘要 + 方法 + 实验拆解）
- 不重复推送
- 直接发布到公众号（非草稿）
- **类别轮换**：奇数天 = SWE 方向，偶数天 = RL/Agent/RLHF 方向

## 2. 关键词与领域

| 领域 | 检索关键词（英文） |
|------|--------------------|
| SWE | software engineering, LLM code generation, automated debugging |
| RL | reinforcement learning, policy optimization, reward modeling |
| Agentic RL | agentic reinforcement learning, LLM agent training |
| RL Infra | RL infrastructure, distributed RL training, RL scaling |
| LLM Agent | LLM agent, tool use, function calling, multi-agent |
| RLHF | RLHF, preference learning, human feedback, alignment |

关键词可在配置文件中动态维护。

## 3. 数据源

| 数据源 | 方式 | 用途 | 稳定性 |
|--------|------|------|--------|
| arXiv API | HTTP Atom API | 主力检索，按关键词+时间 | ✅ 高 |
| HuggingFace Daily Papers | REST API | 热度信号（upvotes） | ✅ 高 |
| Semantic Scholar | REST API (需 key) | 引用数、会议标注 | ⚠️ 无 key 限流严重 |
| Google Scholar (CDP) | Chrome CDP 搜索 | 备选，引用数据 | ⚠️ 不稳定 |

**Phase 1 只用 arXiv + HuggingFace**，Semantic Scholar 等拿到 API key 后接入。

## 4. 论文质量评分

每篇论文计算一个 quality score（0-100），用于排序选择：

```
score = w1 * venue_score      # 顶会加分：ICLR/ICML/NeurIPS/ACL/EMNLP = 30, workshop = 10
      + w2 * citation_score   # 引用数归一化（新论文此项权重低）
      + w3 * hf_upvotes       # HuggingFace 热度
      + w4 * recency_score    # 越新越好
      + w5 * keyword_match    # 与目标领域的匹配度
```

**Phase 1 简化版**：
- arXiv category 匹配（cs.CL, cs.AI, cs.SE, cs.LG, cs.MA）= 基础分
- HuggingFace upvotes > 20 = 高热度加分
- 标题/摘要与关键词的匹配度
- 发布日期（最近 30 天优先）
- 已知顶会名称出现在 comment 字段 = 加分

## 5. 系统架构

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌───────────────┐
│  Cron 触发   │────▶│  论文检索     │────▶│  评分 & 选择  │────▶│  生成讲解文章  │
│  每天 7:00   │     │  arXiv + HF  │     │  去重 + 排序  │     │  大白话精读    │
└─────────────┘     └──────────────┘     └──────────────┘     └───────┬───────┘
                                                                      │
                                                              ┌───────▼───────┐
                                                              │  推送公众号    │
                                                              │  wechat-pub   │
                                                              └───────────────┘
```

## 6. 文件结构

```
skills/paper-recommender/
├── SKILL.md                    # Skill 描述文件
├── config.json                 # 配置（关键词、数据源、评分权重）
├── scripts/
│   ├── paper_recommender.py    # 主脚本（入口）
│   ├── sources/
│   │   ├── arxiv_source.py     # arXiv API 检索
│   │   ├── hf_daily_source.py  # HuggingFace Daily Papers
│   │   └── semantic_scholar.py # Semantic Scholar（Phase 2）
│   ├── scorer.py               # 论文质量评分
│   ├── dedup.py                # 去重（基于论文 ID）
│   └── article_generator.py    # 调用 AI 生成讲解文章
├── data/
│   ├── papers.json             # 论文追踪表（ID, 标题, 分数, 推送时间等）
│   └── published_ids.txt       # 已推送论文 ID 快速查重
└── templates/
    └── paper_explainer.md      # 文章生成的 prompt 模板
```

## 7. 论文追踪表 (papers.json)

```json
[
  {
    "id": "2603.23414",
    "source": "arxiv",
    "title": "SortedRL: Accelerating RL Training...",
    "authors": ["..."],
    "url": "https://arxiv.org/abs/2603.23414",
    "pdf_url": "https://arxiv.org/pdf/2603.23414",
    "published_date": "2026-03-24",
    "categories": ["cs.LG", "cs.AI"],
    "venue": null,
    "score": 72,
    "matched_keywords": ["RL", "RL Infra"],
    "hf_upvotes": 45,
    "fetched_at": "2026-03-25",
    "pushed_at": null,
    "push_media_id": null
  }
]
```

## 8. 每日流程伪代码

```python
def daily_run():
    # 1. 加载配置和历史
    config = load_config()
    history = load_papers_db()
    published_ids = load_published_ids()

    # 2. 多关键词检索
    candidates = []
    for keyword_group in config["keywords"]:
        papers = arxiv_search(keyword_group, max_results=20, days=30)
        candidates.extend(papers)
    
    # 补充 HuggingFace 热门论文
    hf_papers = hf_daily_papers(limit=50)
    candidates.extend(match_hf_to_keywords(hf_papers, config["keywords"]))

    # 3. 去重（已推送 + 本批次内去重）
    candidates = dedup(candidates, published_ids)

    # 4. 评分排序
    scored = score_papers(candidates, config["scoring"])
    best = scored[0]  # 取最高分

    # 5. 下载 PDF，提取全文
    full_text = fetch_paper_content(best)

    # 6. 生成讲解文章（Markdown）
    article_md = generate_explainer(best, full_text, config["prompt_template"])

    # 7. 推送到微信公众号
    result = publish_to_wechat(article_md)

    # 8. 更新追踪表
    update_papers_db(best, result)
```

## 9. 文章生成策略

使用当前 nanobot 的 AI 能力（spawn subagent）来生成文章：

**Prompt 模板要点**：
- 用大白话解释，像给聪明但非本领域的朋友讲
- 结构：**论文阅读报告**格式
  1. 一句话总结
  2. 这篇论文解决什么问题？为什么重要？
  3. 摘要解读（核心贡献）
  4. 方法详解（技术路线拆解，配图说明）
  5. 实验分析（关键实验设置、结果数据、消融实验）
  6. 我的评价（亮点、局限、对领域的影响）
- 保留关键英文术语，中文行文
- 控制在 3000-5000 字（实验部分要详细）
- 微信公众号友好的排版

## 10. 定时任务

```
cron_expr: "0 23 * * *"  # UTC 23:00 = 北京时间 07:00
tz: "UTC"
message: "执行每日论文推荐任务：读取 skills/paper-recommender/SKILL.md 并执行"
```

## 11. Phase 规划

**Phase 1（MVP）**：
- arXiv API 检索 + 简单评分
- AI 生成讲解文章
- 推送到微信公众号草稿箱
- 论文追踪表去重
- Cron 定时触发

**Phase 2**：
- 接入 Semantic Scholar（需申请 API key）
- 引用数、会议信息纳入评分
- Google Scholar CDP 备选
- HuggingFace 热度权重优化

**Phase 3**：
- 多篇速览 + 精读组合
- 读者反馈（阅读量）反哺选题
- 自动调整关键词权重

## 12. 风险与应对

| 风险 | 应对 |
|------|------|
| arXiv API 偶尔慢 | 设置重试 + 超时，失败则跳过当天 |
| 论文 PDF 太长 | 只提取前 N 页或用摘要+关键章节 |
| 生成文章质量不稳定 | Prompt 模板迭代 + 人工抽查 |
| 微信 API 限制 | 先存草稿，手动发布作为兜底 |
| 关键词太泛导致噪音多 | 评分机制过滤 + 人工维护关键词 |

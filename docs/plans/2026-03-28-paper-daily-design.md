# Paper Daily — Design Document

Daily RL paper recommendation skill for nanobot. Searches, scores, interprets, and publishes to WeChat MP.

## Overview

A nanobot skill (`paper-daily`) that runs twice daily via cron:
- **07:00 CST** — focused topic (current priority)
- **20:00 CST** — random topic from pool

Each run: search papers → score & rank → select top 1 → extract figures/tables → generate interpretation → publish to WeChat MP.

## Skill Structure

```
paper-daily/
├── SKILL.md
├── scripts/
│   ├── search_papers.py          # Search + dual-channel scoring
│   ├── fetch_figures.py          # ar5iv/PDF figure & table extraction
│   ├── render_latex.py           # LaTeX → PNG
│   └── requirements.txt
├── references/
│   ├── topics.md                 # Topic config (focus + pool)
│   └── interpretation-guide.md   # Writing style guide + examples
├── assets/
│   └── templates/
│       └── paper-review.md       # Markdown template for interpretation
└── data/                         # Runtime (lives in workspace)
    └── recommended.jsonl         # Dedup record
```

## Module Design

### 1. Paper Search (`scripts/search_papers.py`)

**Data sources:**
- arXiv API (`export.arxiv.org/api/query`) — free, no key
- Semantic Scholar API (`api.semanticscholar.org`) — free, 100 req/5min, provides citation count, venue, influentialCitationCount
- Hugging Face Papers (`huggingface.co/papers`) — daily hot list, community upvotes
- Papers with Code API — GitHub repo star count for papers

**Categories:** cs.LG, cs.AI, cs.CL, cs.SE, cs.MA

**Dual-channel scoring:**

Channel 1 (classic high-quality):
- Top venue papers (NeurIPS, ICML, ICLR, ACL, EMNLP, AAAI) from past 6 months
- High citation count

Channel 2 (fresh frontier):
- Past 1-2 weeks on arXiv
- Quality signals: HF upvotes, GitHub stars, social buzz, citation velocity

Combined score:
```
score = w1 * venue_bonus
      + w2 * citation_score        # normalized by paper age
      + w3 * recency_score          # higher for newer
      + w4 * hf_upvotes
      + w5 * github_stars
      + w6 * social_buzz
```

Weights tunable in topics.md. New papers win via recency + community signals; established papers win via venue + citations.

**CLI interface:**
```bash
python scripts/search_papers.py --topic "Agentic RL" --exclude data/recommended.jsonl --top 1
```

**Output:** JSON with title, arxiv_id, abstract, authors, pdf_url, venue, score, score_breakdown.

### 2. Figure & Table Extraction (`scripts/fetch_figures.py`)

**Primary:** ar5iv HTML (`ar5iv.labs.arxiv.org/abs/{id}`)
- Extract `<figure>` elements: `<img>` src + `<figcaption>` text
- Extract `<table>` elements: clean HTML with caption
- Download images to `/tmp/paper-daily/{id}/`

**Fallback:** PDF extraction via pymupdf
- Download PDF from arXiv
- Extract embedded images by page
- Heuristic: skip tiny images (logos, icons), keep figures > 200x200px

**CLI:**
```bash
python scripts/fetch_figures.py --arxiv-id 2403.12345 --output /tmp/paper-daily/2403.12345/
```

**Output:** `figures.json` — `[{path, caption, type: "figure"|"table", index}]`

### 3. LaTeX Rendering (`scripts/render_latex.py`)

Uses `matplotlib.mathtext` to render LaTeX strings to PNG.

```bash
python scripts/render_latex.py --latex "Q(s,a) = r + \gamma \max_{a'} Q(s', a')" --output /tmp/formula_01.png --dpi 150
```

Transparent background, suitable for embedding in WeChat articles.

### 4. Interpretation Generation (Agent LLM)

Agent reads the paper (abstract + ar5iv full text or PDF) and writes interpretation following `references/interpretation-guide.md`.

**Target audience:** RL practitioners who may lack specific sub-domain background.

**Article structure:**

```markdown
# {Paper Title}

> One-sentence summary

## Background: Why This Matters
- Problem context in plain language (2-3 paragraphs)
- What gap exists, why previous approaches fall short

## Method: How It Works
- Core idea in plain language first
- Key technical details with figures from the paper
- Important formulas rendered as images, each followed by
  a "plain language translation" paragraph
- Architecture/method diagrams from paper figures

## Experiments: Does It Work?
- Key results in Markdown tables (rebuilt from paper)
- 1-2 most compelling figures from the paper
- Comparison with baselines, what stands out

## My Take
- Significance for the RL community
- Limitations and open questions
- Connection to related work
- Potential future directions

---
Paper: [link] | Code: [link] | Daily Paper Rec by {author}
```

**Writing style:**
- Accessible but deep — "plain language with substance"
- Every formula gets a human-readable explanation
- Figures and tables are annotated, not just shown
- "My Take" section provides genuine insight, not generic praise

### 5. WeChat Publishing

Reuse `wechat-article-publisher` (installed from ClawHub).

Flow:
1. Agent saves interpretation as `/tmp/paper-review-{date}-{slot}.md`
2. Figures already downloaded to `/tmp/paper-daily/{id}/`
3. Formula PNGs rendered to `/tmp/paper-daily/{id}/formulas/`
4. All images uploaded to WeChat media library via `publish_wechat.py`
5. Markdown images replaced with WeChat media URLs
6. `publish_wechat.py /tmp/paper-review-{date}-{slot}.md --template standard --publish`

Cover image: auto-generated by Pillow (paper title + topic-colored gradient).

### 6. Topic Configuration (`references/topics.md`)

```markdown
## Focus Topic (07:00 push)
Agentic RL

## Topic Pool (20:00 random pick)
- RL Training / RL Infrastructure
- RL + Code Generation / SWE Agent
- Multi-Agent RL
- RLHF / RLAIF / Reward Modeling
- Offline RL / Model-based RL
- RL for LLM Reasoning

## Scoring Weights
w1_venue: 0.25
w2_citation: 0.15
w3_recency: 0.25
w4_hf_upvotes: 0.15
w5_github_stars: 0.10
w6_social_buzz: 0.10
```

Agent can edit this file to adjust topics and weights.

### 7. Dedup Record (`data/recommended.jsonl`)

```jsonl
{"arxiv_id":"2403.12345","title":"...","topic":"Agentic RL","date":"2026-03-28","slot":"morning","score":0.87}
```

`search_papers.py` reads this file and excludes already-recommended IDs.

### 8. Cron Setup

Two cron jobs via nanobot cron skill:

```
cron(action="add", message="Run paper-daily: morning push, use focus topic", cron_expr="0 7 * * *", tz="Asia/Shanghai")
cron(action="add", message="Run paper-daily: evening push, random topic from pool", cron_expr="0 20 * * *", tz="Asia/Shanghai")
```

### 9. Dependencies

```
arxiv              # arXiv API client
requests           # HTTP
pymupdf            # PDF figure extraction (fallback)
matplotlib         # LaTeX formula rendering
Pillow             # Cover image generation
beautifulsoup4     # HTML parsing
lxml               # HTML parser backend
feedparser         # Optional: RSS feeds
```

Plus `wechat-article-publisher` skill installed from ClawHub.

### 10. End-to-End Flow

```
Cron triggers → Agent receives task message
→ Read topics.md (determine topic for this slot)
→ Run search_papers.py (search + score + dedup → select paper)
→ Read paper (ar5iv HTML or PDF via agent tools)
→ Run fetch_figures.py (extract figures + tables)
→ Run render_latex.py (for key formulas)
→ Generate interpretation (LLM, following interpretation-guide.md)
→ Save as Markdown with image references
→ Run publish_wechat.py (upload images + create draft + publish)
→ Append to recommended.jsonl
→ Report result
```

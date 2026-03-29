---
name: paper-daily
description: "Daily RL paper recommendation and interpretation for WeChat MP. Searches arXiv/Semantic Scholar/HuggingFace, scores papers via dual-channel (top-venue + fresh-frontier), generates deep interpretations with figures/formulas/tables, and publishes to WeChat. Use when: (1) cron triggers morning/evening paper push, (2) user asks for paper recommendation, (3) user says '推荐论文', '论文日推', 'paper daily', 'recommend a paper'."
---

# Paper Daily

Daily RL paper search, interpretation, and WeChat MP publishing.

## Workflow

### 1. Determine Topic

Read `references/topics.md`:
- Morning (07:00): use the Focus Topic
- Evening (20:00): randomly pick one from Topic Pool

### 2. Search Papers

```bash
python scripts/search_papers.py \
  --topic "TOPIC" \
  --exclude data/recommended.jsonl \
  --topics-file references/topics.md \
  --top 1
```

Output: JSON array with top paper metadata.

### 3. Read the Paper

Fetch full text for interpretation:
- Primary: `curl -s "https://ar5iv.labs.arxiv.org/abs/{arxiv_id}"` then extract article text
- Fallback: download PDF and read via tools

### 4. Extract Figures and Tables

```bash
python scripts/fetch_figures.py --arxiv-id {arxiv_id} --output /tmp/paper-daily/{arxiv_id}/
```

Output: `figures.json` with local paths to downloaded figures and table HTML.

### 5. Render Key Formulas

For each key formula in the paper:

```bash
python scripts/render_latex.py --latex "FORMULA" --output /tmp/paper-daily/{arxiv_id}/formulas/f{N}.png
```

### 6. Write Interpretation

Follow `references/interpretation-guide.md` for style and structure.
Use `assets/templates/paper-review.md` as the skeleton.

Key rules:
- Every formula image is followed by a "翻译成人话" paragraph
- Tables are written as Markdown tables (not screenshots)
- Figures include captions
- "我的思考" section has genuine insight
- Target length: 2000-3000 Chinese characters

Save to `/tmp/paper-review-{date}-{slot}.md`.

### 7. Publish to WeChat

Ensure `wechat-article-publisher` skill is installed. Then:

```bash
python ~/.nanobot/workspace/skills/wechat-article-publisher/scripts/publish_wechat.py \
  /tmp/paper-review-{date}-{slot}.md \
  --template standard \
  --publish --status
```

If publish_wechat.py is not at that path, check `bots/bot-chat/workspace/skills/wechat-article-publisher/`.

### 8. Record Recommendation

Append to `data/recommended.jsonl`:

```json
{"arxiv_id":"ID","title":"TITLE","topic":"TOPIC","date":"YYYY-MM-DD","slot":"morning|evening","score":0.87}
```

Create the file if it doesn't exist.

## Manual Trigger

User can say "推荐一篇论文" or "recommend a paper" to trigger a one-off run.
In manual mode, ask which topic to use if not specified.

## Topic Management

Edit `references/topics.md` to change focus topic or topic pool.
User can say "换个重点 topic" to update the focus topic.

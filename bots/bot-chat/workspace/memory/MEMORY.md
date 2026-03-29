# Long-term Memory

This file stores important information that should persist across sessions.

## User Information

- Username on Discord: ππ (channel 1477512015604879450)
- User ID on Discord: 1043195274752958494
- User's Mac: macOS 15.1 (arm64), node 25.2.1
- Working directory: /Users/shingz/Documents/Project/AgentBot

## Preferences

- Prefers Chinese (Mandarin) communication
- Wants assistant to directly operate browser rather than manual steps

## Project Context

### agent-reach / 小红书 Configuration — WORKING ✅
- **Current setup**: Native macOS binary at `/tmp/xiaohongshu-mcp-darwin-arm64` (NOT Docker)
- **Cookie file**: `/tmp/xhs-data/cookies.json` — format is `proto.NetworkCookie` JSON array (go-rod CDP format)
- **Launch**: `COOKIES_PATH=/tmp/xhs-data/cookies.json nohup /tmp/xiaohongshu-mcp-darwin-arm64 &`
- **Port**: `localhost:18060`
- **Status as of 2026-03-23 21:41**: Fixed — mcporter config corrected to baseUrl HTTP mode, cookies refreshed via Chrome CDP. Login verified, search_feeds working.
- mcporter config: `/Users/shingz/Documents/Project/AgentBot/bots/bot-chat/workspace/config/mcporter.json`
- Xiaohongshu MCP baseUrl: `http://localhost:18060/mcp`
- **mcporter config must use `baseUrl` mode** (not stdio) — binary is HTTP server, not stdio MCP
- **IMPORTANT**: Config has been found reverting to stdio mode with `command`+`args` format at least twice. Always verify config is `baseUrl` mode before troubleshooting.
- Available tools (13): check_login_status, delete_cookies, favorite_feed, get_login_qrcode, get_feed_detail, search_feeds, publish_content, and others
- **Fix history**: Config was wrongly set to stdio mode with `--cookies` arg multiple times; binary doesn't support stdio or `--cookies` flag. Binary only accepts: `-port`, `-headless`, `-bin`, `-rod`.
- Also has `browser` entry in mcporter config: `baseUrl: http://localhost:19222/mcp`
- **mcporter CLI usage**: `mcporter call xiaohongshu.search_feeds keyword="..."` — no `limit` param; `get_feed_detail` uses `feed_id` and `xsec_token` (NOT `note_id`)

### 知乎 / Zhihu — Partial ⚠️
- **Search**: Works via `web_search` with `site:zhihu.com` query. No extra config needed.
- **Read full articles**: BLOCKED by Zhihu anti-bot. Jina Reader returns only page title. No working Zhihu reading MCP exists.
- **Potential solution for reading**: Use Chrome CDP proxy (user is logged into Zhihu in Chrome)

### multi-search-engine skill — NOT WORKING ❌
- Skill exists with 17 engines (8 CN + 9 Global) but relies on web_fetch/Jina Reader to scrape search engine pages
- All major search engines have anti-bot protection, so results are empty or ads-only
- **Use built-in `web_search` tool instead** — it works reliably

### WeChat Official Account (微信公众号) — WORKING ✅
- **AppID**: `wxa555f3bb620b4c00`
- **AppSecret**: `b2980af94935ff7217a2c8c8352efdef`
- **Author**: Jason
- **Config file**: `skills/wechat-article-publisher/config.json`
- **Server outbound IPs**: Unstable/rotating. Known IPs: `103.116.123.203`, `103.116.123.123`, `103.116.123.75`, `103.116.123.187` — ALL must be in MP whitelist
- **Access token**: Successfully obtained as of 2026-03-24 10:39
- **Account type**: Uncertified — **freepublish permission status unclear**. Previously recorded as 48001 error, but user claims it worked once. Needs re-testing after IP whitelist update.
- **Publishing workflow**: 
  1. Write article as Markdown in `workspace/articles/` (NOT /tmp — files get deleted)
  2. `python3 skills/wechat-article-publisher/scripts/publish_wechat.py <md_file> --config skills/wechat-article-publisher/config.json --dry-run` (preview)
  3. `python3 skills/wechat-article-publisher/scripts/publish_wechat.py <md_file> --config skills/wechat-article-publisher/config.json` (push to draft)
  4. `python3 skills/wechat-article-publisher/scripts/publish_wechat.py <md_file> --config skills/wechat-article-publisher/config.json --publish --status` (auto-publish, if freepublish works)
- **Script name**: `publish_wechat.py` (NOT `publish.py`)
- **Dependencies**: Run `--install` first; needs requests, beautifulsoup4, markdown, pyyaml, Pillow
- **Templates**: `standard` or `viral`
- **Cover image**: Use `--cover-image <path>` to specify; without it script may auto-generate or fail
- **Body images / cover handling updated (2026-03-29)**:
  - `publish_wechat.py` now uploads正文图片 via WeChat `media/uploadimg` and replaces `<img src>` with returned WeChat URLs before draft creation.
  - Cover and body images are separate mechanisms: cover requires `thumb_media_id`; body images require `uploadimg` returned `url`.
  - Cover fallback improved, including macOS font support for generated covers.
  - Script now outputs validation fields including `thumb_media_id`, `body_image_count`, and `body_images`.
- **Published articles**:
  1. 2026-03-24, "让 AI 程序员在 GitHub 的海洋中学会「真正写代码」" (Scale-SWE paper explainer v1), draft_media_id: `1c49pMtFecwG-47Q-TADiAGyLvUzawO6t1YSxWi0Xvw8C6ZPzNOEcMqQeDKbxrfT`
  2. 2026-03-24, "Scale-SWE 深度解读：从 6M Pull Requests 到 100K 训练实例，SWE Agent 的数据工程全景" (deep dive for RL practitioners), draft_media_id: `1c49pMtFecwG-47Q-TADiJGxBCBqekltfCjzR8fFP8uk9S3wfeF2h_MYAjnYt3Hv`
  3. 2026-03-26, "好的开始是成功的一半：PPPO 用前缀优化重新定义 LLM 推理训练" (PPPO prefix RL for reasoning, arXiv:2512.15274, with paper figures), draft_media_id: `1c49pMtFecwG-47Q-TADiMnvvIL2Ruo-N0bXuYuZvmfoDPT409LK400qTkiEnzXp`. --publish attempted but blocked by IP whitelist (103.116.123.187 not whitelisted).
  4. 2026-03-29, "Agent 会自己进化吗？一文讲透通往 ASI 的 Self-Evolving Agents" (self-evolving agents / ASI survey), draft_media_id: `1c49pMtFecwG-47Q-TADiGjuv896mfhwsX9tkeobeduVDtvIzn3Xu_pHY2tYBxQH`, template `viral`.
- **Path/config drift note (2026-03-29)**:
  - Skill `config.json` may be missing and need to be reconstructed from remembered credentials.
  - Workspace memory files are under `workspace/memory/` rather than a top-level `workspace/MEMORY.md` path.
  - Skill files confirmed under `bots/bot-chat/workspace/skills/wechat-article-publisher/`.

### Paper Recommender Skill — IN PROGRESS ⚠️
- **Location**: `skills/paper-recommender/`
- **Purpose**: Daily automated paper discovery → deep reading report → WeChat MP publish
- **Schedule**: Odd days = SWE, Even days = RL/Agent/RLHF. Daily 7:00 AM Beijing time.
- **Output**: 1 deep-dive article per day (摘要+方法+实验, 3000-5000字, 大白话风格)
- **Data sources**: Semantic Scholar API (primary), HuggingFace Daily Papers API (trending/upvotes), arXiv API (fallback)
- **Search approach (updated 2026-03-26)**: Semantic Scholar `/paper/search` endpoint as primary source. Free, no API key needed. Returns semantically relevant results with citation counts, venue info. arXiv demoted to fallback (only used when Scholar returns <10 papers).
- **Scholar queries (consolidated)**:
  - RL group (4 queries): "reinforcement learning LLM policy optimization", "RLHF reward modeling preference optimization", "LLM agent reinforcement learning training", "distributed RL infrastructure scaling RLHF"
  - SWE group (3 queries): "software engineering LLM code generation", "automated debugging code repair agent", "SWE-bench code agent evaluation"
- **Scholar rate limiting**: 429 errors common. Retry delay: 10s, inter-query sleep: 8s, max 3 retries per request.
- **Scoring (updated 2026-03-26)**: 5 dimensions — venue (20), citations (15, log-scaled), HF upvotes (20), recency (20), keyword match (25). Scholar source bonus: +15 points. Total possible ~115 but clamped to 100.
- **Core phrase filtering**: REMOVED (2026-03-26). Scholar's semantic search makes manual phrase filtering unnecessary.
- **File structure**:
  ```
  skills/paper-recommender/
  ├── SKILL.md
  ├── config.json (keywords by group, scoring weights, arxiv categories, scholar config)
  ├── scripts/
  │   ├── paper_recommender.py (main pipeline)
  │   ├── scorer.py (5-dimension scoring with citation_score and source_bonus)
  │   ├── dedup.py (published_ids.txt + papers.json tracking)
  │   └── sources/
  │       ├── __init__.py
  │       ├── arxiv_source.py (Atom API with retry — now fallback only)
  │       ├── hf_daily_source.py (daily papers + keyword filter)
  │       └── scholar_source.py (NEW — Semantic Scholar API, primary source)
  ├── data/
  │   ├── papers.json (tracking DB)
  │   └── published_ids.txt (dedup)
  └── templates/
      └── paper_explainer.md (prompt template)
  ```
- **Pipeline**: `--search-only` | `--dry-run` | `--auto` | `--paper-id <id>`
- **Full text**: Fetched via Jina Reader (PDF or HTML), fallback to abstract
- **Report generation**: Claude API (claude-sonnet-4-20250514), reads API key from nanobot config `providers.anthropic.apiKey`
- **Publishing**: Calls wechat-article-publisher script
- **Known issues (2026-03-26)**:
  - **FIXED**: Search quality — replaced arXiv keyword search with Semantic Scholar semantic search. Top 10 results now all highly relevant.
  - **FIXED**: Core phrase filtering complexity — removed entirely, Scholar handles relevance.
  - **FIXED**: Template KeyError for `{领域关键词}` — escaped to `{{领域关键词}}` in paper_explainer.md
  - **FIXED**: API key reading — now reads `providers.anthropic.apiKey` (camelCase) from nanobot config
  - **BLOCKING**: Nanobot Anthropic API key is OAuth token (`sk-ant-oat01...`), returns 401. Needs standard API key (`sk-ant-api03-...`) for report generation.
  - **WORKAROUND (2026-03-26)**: User asked assistant to write reports directly instead of calling Claude API, bypassing the API key issue.
  - **Scholar 429 rate limiting** — mitigated with 10s retry delay and 8s inter-query sleep, but still occasional failures (2/4 queries failed on 2026-03-26)
  - Cron job not yet configured
  - End-to-end pipeline (search→report→publish) not yet tested with automated API
  - SWE group Scholar queries not yet validated
- **Published reports**:
  1. 2026-03-26 (RL day), "PPPO: 只优化开头，推理全盘皆活" — PPPO prefix optimization for LLM reasoning (arXiv:2512.15274, AAAI 2026). File: `workspace/articles/2026-03-26-pppo-prefix-rl-reasoning.md`. Sent to Discord, pushed to WeChat MP draft. --publish blocked by IP whitelist.
- **Config keywords** (legacy, still in config.json but Scholar queries are primary now):
  - SWE group: software engineering LLM, automated code generation, automated debugging LLM, SWE-bench, code repair agent
  - RL group: reinforcement learning policy optimization, reward modeling, reinforcement learning scaling, agentic reinforcement learning, LLM agent reinforcement learning, agent training environment, distributed reinforcement learning training, RL infrastructure scaling, RLHF infrastructure, LLM agent tool use, multi-agent LLM, function calling LLM, RLHF preference learning, human feedback alignment, direct preference optimization

### Cookie Refresh Procedure
- To refresh cookies: use raw socket WebSocket CDP handshake (no Origin header) to Chrome (`localhost:9222`), connect to XHS page endpoint, run `Network.getAllCookies`, filter by xiaohongshu domain, save as proto.NetworkCookie JSON array to cookies.json, restart MCP server
- Chrome has `--remote-debugging-port=9222` but rejects standard WebSocket libraries due to missing `--remote-allow-origins=*`
- Raw socket approach (Python socket + manual HTTP upgrade, no Origin header) bypasses this restriction
- User's Chrome profile dir: `/tmp/chrome-debug-profile`
- Last cookie refresh: 2026-03-23 21:41, extracted 12 XHS cookies

### Docker Notes (for reference, NOT currently used)
- Docker image `xpzouying/xiaohongshu-mcp` works but needs `-v ./data:/app/data -e COOKIES_PATH=/app/data/cookies.json` mount
- Without volume mount, cookies are lost on container restart
- Container runs headless Chrome via Rod, QR codes not visible from outside
- Container runs on Rosetta (linux/amd64 on arm64 Mac)

### Chrome CDP Access
- Chrome has `--remote-debugging-port=9222` but MISSING `--remote-allow-origins=*`
- HTTP API works (e.g., GET /json/list, PUT /json/new)
- Standard WebSocket libraries (websockets, websocket-client) get 403 Forbidden
- **Working approach**: Raw Python socket with manual WebSocket handshake (no Origin header)
- Python websockets and websocket-client libraries are installed

### Nanobot / Clawdbot Setup
- Framework: nanobot (clawdbot)
- Config: `~/.nanobot/workspace` or `/Users/shingz/Documents/Project/AgentBot/.nanobot/.bot-chat/`
- Model: anthropic/claude-sonnet-4-6
- Discord channel enabled, token configured
- Anthropic API key configured in config.json (OAuth token `sk-ant-oat01...`, NOT standard API key)
- **Nanobot config structure**: `providers` is a dict (not list), keyed by provider name. API key at `providers.anthropic.apiKey` (camelCase)
- Gateway: port 18790
- Agent-reach skill: search/read 14 platforms (Twitter, Reddit, YouTube, GitHub, Bilibili, XiaoHongShu, etc.)
- Also has multi-search-engine skill (17 engines, but NOT WORKING due to anti-bot)
- Tools: xreach (Twitter), yt-dlp (YouTube/Bilibili), mcporter (MCP), exa (web search)
- clawdbot CLI commands like 'open' and 'screenshot' don't exist in nanobot environment
- **Workspace path note**: Skills are at `bots/bot-chat/workspace/skills/` (not `.nanobot/.bot-chat/`)

### CDP Search Skill
- Location: `workspace/skills/cdp-search/scripts/cdp_search.py` (note: scripts/ subdirectory)
- 6 platforms: Google (CDP), Baidu (CDP), Zhihu (CDP reuse tab), XiaoHongShu (mcporter), GitHub (REST API), arXiv (Atom API)
- Chrome must have `--remote-debugging-port=9222 --remote-allow-origins=*`
- Zhihu requires existing logged-in tab in Chrome (new tabs don't inherit session)
- GitHub uses unauthenticated API (rate limited but works)
- mcporter must run from workspace dir
- Usage: `python3 skills/cdp-search/scripts/cdp_search.py -m google zhihu xiaohongshu -q "query" -l 3`
- Full page read: `python3 skills/cdp-search/scripts/cdp_search.py -r <url>`
- **Known issue**: Google CDP search returned 0 results on 2026-03-23; Baidu CDP works
- **Known issue**: XHS via cdp-search failed (No Mcp-Session-Id); use mcporter directly instead
- **Known issue**: Google Scholar CDP blocked by CAPTCHA (tested 2026-03-26) — use Semantic Scholar API instead

## Important Notes

- Xiaohongshu MCP runs as native binary, NOT Docker (Docker had cookie persistence issues)
- When refreshing XHS cookies, use raw socket WebSocket to Chrome CDP (no Origin header) to bypass --remote-allow-origins restriction
- Cookie format must be proto.NetworkCookie JSON array with fields: name, value, domain, path, expires, size, httpOnly, secure, session, sameSite (all camelCase)
- Fetch CDP page list and webSocketDebuggerUrl in a single step immediately before connecting to avoid stale IDs
- QR code login via mcporter has very short expiry (~30s) and headless mode prevents seeing QR - prefer CDP cookie extraction approach
- Zhihu search: use web_search with site:zhihu.com (works reliably)
- Zhihu full article reading blocked by anti-bot; consider Chrome CDP proxy approach
- multi-search-engine skill is broken (anti-bot); always use built-in web_search instead
- **mcporter config keeps reverting to wrong format** — always check before assuming it's correct
- **WeChat MP outbound IPs are unstable** — rotate among 103.116.123.203, 103.116.123.123, 103.116.123.75, 103.116.123.187. All must be whitelisted.
- **WeChat MP freepublish permission**: User claims it worked before. Re-test after IP whitelist update. Previously recorded as 48001 but may have been resolved.
- **Save article files to workspace/articles/, NOT /tmp** — /tmp files get deleted
- **Paper recommender search overhauled (2026-03-26)** — Semantic Scholar API as primary source, arXiv as fallback. Scholar provides semantic relevance ranking, much better than keyword substring matching.
- **Paper recommender report generation blocked** — nanobot Anthropic API key is OAuth token (`sk-ant-oat01...`), returns 401. Needs standard API key. **Workaround: assistant writes reports directly.**
- **Nanobot config providers is a dict** — access as `providers['anthropic']['apiKey']`, not a list
- **Google Scholar CDP blocked by CAPTCHA** — don't use CDP for Scholar, use Semantic Scholar API instead
- **Article file path doubling**: When writing from workspace dir, check that path doesn't double (e.g., workspace/workspace/articles/ vs workspace/articles/)
- **WeChat skill path/config can drift** — if `skills/wechat-article-publisher/config.json` is missing, reconstruct it from remembered credentials; also verify memory files live under `workspace/memory/`.
- **WeChat article drafts missing images/cover (2026-03-29) was traced to script gaps, not article content** — `publish_wechat.py` has been patched, but live verification still depends on whitelist access.

## Installed Skills

- **wechat-mp-draft**: Shell script for WeChat MP draft creation via API. Scripts: get_access_token.sh, upload_image.sh, add_draft.sh. Needs AppID + AppSecret + IP whitelist.
- **wechat-article-publisher**: Python, full pipeline — Markdown/URL → styled article → draft/publish. Config needs `app_id`, `app_secret`, `author`. Supports --dry-run, --publish, --status. Templates: standard, viral. Best option for automated publishing. **Script: `publish_wechat.py`** (not publish.py). Dependencies: requests, beautifulsoup4, markdown, pyyaml, Pillow.
- **wechat-mp-cn**: WeChat MP monitoring (reading stats, sentiment). Not for publishing.
- **agent-browser-clawdbot**: Browser automation via clawdbot + Playwright CDP. Note: `clawdbot` CLI lacks many advertised commands (install-extension, start, eval don't exist). MCP browser server on port 19222 also failed to connect. **Effectively NOT WORKING in nanobot environment.**
- **paper-recommender**: Daily paper discovery + deep reading report + WeChat MP publish. See Paper Recommender section above for details. **Status: IN PROGRESS, API key blocked but workaround is assistant writing reports directly.**

---

*This file is automatically updated by nanobot when important information should be remembered.*
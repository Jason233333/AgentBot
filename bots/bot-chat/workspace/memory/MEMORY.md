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
- **Server outbound IP**: `103.116.123.203` (added to MP whitelist; note: `curl ifconfig.me` returns different IP 188.253.7.43 — the actual outbound IP for WeChat API calls is 103.116.123.203)
- **Access token**: Successfully obtained as of 2026-03-24 10:39
- **Publishing workflow**: 
  1. Write article as Markdown in `workspace/articles/`
  2. `python3 skills/wechat-article-publisher/scripts/publish_wechat.py <md_file> --config skills/wechat-article-publisher/config.json --dry-run` (preview)
  3. `python3 skills/wechat-article-publisher/scripts/publish_wechat.py <md_file> --config skills/wechat-article-publisher/config.json` (push to draft)
  4. Optional: `--publish --status` to submit for publication
- **Script name**: `publish_wechat.py` (NOT `publish.py`)
- **Dependencies**: Run `--install` first; needs requests, beautifulsoup4, markdown, pyyaml, Pillow
- **Templates**: `standard` or `viral`
- **Cover image**: Use `--cover-image <path>` to specify; without it script may auto-generate or fail
- **First article published**: 2026-03-24, "让 AI 程序员在 GitHub 的海洋中学会「真正写代码」" (Scale-SWE paper explainer), draft_media_id: `1c49pMtFecwG-47Q-TADiAGyLvUzawO6t1YSxWi0Xvw8C6ZPzNOEcMqQeDKbxrfT`

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
- Anthropic API key configured in config.json
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
- **WeChat MP outbound IP** is 103.116.123.203 (different from ifconfig.me result)

## Installed Skills

- **wechat-mp-draft**: Shell script for WeChat MP draft creation via API. Scripts: get_access_token.sh, upload_image.sh, add_draft.sh. Needs AppID + AppSecret + IP whitelist.
- **wechat-article-publisher**: Python, full pipeline — Markdown/URL → styled article → draft/publish. Config needs `app_id`, `app_secret`, `author`. Supports --dry-run, --publish, --status. Templates: standard, viral. Best option for automated publishing. **Script: `publish_wechat.py`** (not publish.py). Dependencies: requests, beautifulsoup4, markdown, pyyaml, Pillow.
- **wechat-mp-cn**: WeChat MP monitoring (reading stats, sentiment). Not for publishing.
- **agent-browser-clawdbot**: Browser automation via clawdbot + Playwright CDP. Note: `clawdbot` CLI lacks many advertised commands (install-extension, start, eval don't exist). MCP browser server on port 19222 also failed to connect. **Effectively NOT WORKING in nanobot environment.**

---

*This file is automatically updated by nanobot when important information should be remembered.*
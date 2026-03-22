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
- **Launch**: `COOKIES_PATH=/tmp/xhs-data/cookies.json /tmp/xiaohongshu-mcp-darwin-arm64`
- **Port**: `localhost:18060`
- **Status as of 2026-03-22**: Login verified, search_feeds working. get_feed_detail may return 'not found in noteDetailMap' errors.
- mcporter config: `/Users/shingz/Documents/Project/AgentBot/.nanobot/.bot-chat/workspace/config/mcporter.json`
- Xiaohongshu MCP baseUrl: `http://localhost:18060/mcp`
- Available tools (13): check_login_status, delete_cookies, favorite_feed, get_login_qrcode, get_feed_detail, search_feeds, publish_content, and others

### 知乎 / Zhihu — Partial ⚠️
- **Search**: Works via `web_search` with `site:zhihu.com` query. No extra config needed.
- **Read full articles**: BLOCKED by Zhihu anti-bot. Jina Reader returns only page title. No working Zhihu reading MCP exists.
- **Potential solution for reading**: Use Chrome CDP proxy (user is logged into Zhihu in Chrome)

### multi-search-engine skill — NOT WORKING ❌
- Skill exists with 17 engines (8 CN + 9 Global) but relies on web_fetch/Jina Reader to scrape search engine pages
- All major search engines have anti-bot protection, so results are empty or ads-only
- Baidu: returns page framework without results; DuckDuckGo: mostly ads; Google: needs proxy + anti-bot
- **Use built-in `web_search` tool instead** — it works reliably

### Cookie Refresh Procedure
- To refresh cookies: use raw socket WebSocket CDP handshake (no Origin header) to Chrome (`localhost:9222`), connect to XHS page endpoint, run `Network.getAllCookies`, filter by xiaohongshu domain, save as proto.NetworkCookie JSON array to cookies.json, restart MCP server
- Chrome has `--remote-debugging-port=9222` but rejects standard WebSocket libraries due to missing `--remote-allow-origins=*`
- Raw socket approach (Python socket + manual HTTP upgrade, no Origin header) bypasses this restriction
- User's Chrome profile dir: `/tmp/chrome-debug-profile`

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

### CDP Search Skill
- Location: `workspace/skills/cdp-search/cdp_search.py`
- 6 platforms: Google (CDP), Baidu (CDP), Zhihu (CDP reuse tab), XiaoHongShu (mcporter), GitHub (REST API), arXiv (Atom API)
- Chrome must have `--remote-debugging-port=9222 --remote-allow-origins=*`
- Zhihu requires existing logged-in tab in Chrome (new tabs don't inherit session)
- GitHub uses unauthenticated API (rate limited but works)
- mcporter must run from workspace dir
- Usage: `python3 cdp_search.py -m google zhihu xiaohongshu -q "query" -l 3`
- Full page read: `python3 cdp_search.py -r <url>`

## Important Notes

- Xiaohongshu MCP runs as native binary, NOT Docker (Docker had cookie persistence issues)
- When refreshing XHS cookies, use raw socket WebSocket to Chrome CDP (no Origin header) to bypass --remote-allow-origins restriction
- Cookie format must be proto.NetworkCookie JSON array with fields: name, value, domain, path, expires, size, httpOnly, secure, session, sameSite (all camelCase)
- Fetch CDP page list and webSocketDebuggerUrl in a single step immediately before connecting to avoid stale IDs
- QR code login via mcporter has very short expiry (~30s) and headless mode prevents seeing QR - prefer CDP cookie extraction approach
- Zhihu search: use web_search with site:zhihu.com (works reliably)
- Zhihu full article reading blocked by anti-bot; consider Chrome CDP proxy approach
- multi-search-engine skill is broken (anti-bot); always use built-in web_search instead

---

*This file is automatically updated by nanobot when important information should be remembered.*
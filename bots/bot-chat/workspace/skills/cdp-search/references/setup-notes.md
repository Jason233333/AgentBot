# CDP Search — Setup Notes & Pitfalls

This documents the issues encountered while building the skill, for future reference.

## Chrome CDP Setup

### Required Launch Flags

```
--remote-debugging-port=9222
--remote-allow-origins=*
```

Without `--remote-allow-origins=*`, all WebSocket connections get **403 Forbidden**.
HTTP endpoints (`/json/list`, `/json/new`) work without this flag, but JS execution requires WebSocket.

### Finding Chrome Launch Command

```bash
ps aux | grep -i 'chrome' | grep remote-debugging
```

To restart Chrome with correct flags, kill existing process and relaunch with both flags.

### WebSocket Connection

Use `websocket-client` library (not `websockets`):

```python
import websocket
ws = websocket.create_connection(ws_url, timeout=30)
```

Before `--remote-allow-origins=*` was added, raw socket WebSocket handshake (no Origin header) was the only workaround.

## Zhihu Auth Issue

New CDP tabs (`/json/new`) do **not** inherit the login session from the user's main Chrome profile.
The debug profile at `/tmp/chrome-debug-profile` is separate.

**Solution**: Reuse an existing tab that already has zhihu.com open and logged in.
The script uses `Page.navigate` on the existing tab instead of creating new ones.

If zhihu returns "未搜索到相关内容", it means the tab lost its session — user needs to re-login.

## XiaoHongShu Login

### Method: mcporter + native binary

1. Native binary: `/tmp/xiaohongshu-mcp-darwin-arm64` (downloaded from GitHub releases)
2. Cookie path: `/tmp/xhs-data/cookies.json`
3. MCP endpoint: `http://localhost:18060/mcp`
4. mcporter config: `workspace/config/mcporter.json`

### Login Procedure

Cookies are extracted from Chrome CDP using `Network.getCookies` for `.xiaohongshu.com` domain,
then converted to camelCase go-rod format (`name`, `value`, `domain`, `path`, `expires`, `httpOnly`, `secure`, `sameSite`).

The binary also accepts `XHS_COOKIES_PATH` env var or `--cookies <path>` flag.

### Cookie Format (camelCase go-rod)

```json
[
  {
    "name": "cookie_name",
    "value": "cookie_value",
    "domain": ".xiaohongshu.com",
    "path": "/",
    "expires": 1742000000,
    "httpOnly": true,
    "secure": true,
    "sameSite": "None"
  }
]
```

Note: Field names must be camelCase (not PascalCase). `sameSite` values: `"Strict"`, `"Lax"`, `"None"` (capitalized).

## GitHub Search

CDP scraping failed due to dynamic class names. Switched to GitHub REST API:

```
GET https://api.github.com/search/repositories?q=<query>&sort=stars&per_page=3
```

No auth needed. Rate limit: 10 requests/minute unauthenticated.
If `gh` CLI is installed and authed, can use `gh api` for higher limits.

## arXiv Search

CDP scraping had slow JS rendering. Switched to Atom API:

```
GET https://export.arxiv.org/api/query?search_query=all:<query>&max_results=3&sortBy=relevance
```

Returns XML (Atom feed). No auth needed. Reliable and fast.

## Baidu Selector Notes

Baidu results use `h3.t` or `h3[class*="t"]` for titles, `.c-container` for result cards.
Snippets in `.c-abstract` or `[class*="content-right"]`.
Baidu sometimes returns recommendation cards and hot search instead of real results — filter by checking `h3` presence.

## Google Selector Notes

Google no longer uses `div.g` for results (as of 2026). Results are identified by `h3` elements.
Snippets found by walking parent `[data-hveid]` containers and looking for long `span`/`div` text (>40 chars).
Filter out `google.com/search` URLs to avoid "People also ask" links.

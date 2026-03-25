---
name: cdp-search
description: >
  Unified multi-platform search via Chrome CDP, GitHub API, arXiv API, and mcporter MCP.
  6 platforms: Google, Baidu, Zhihu, XiaoHongShu, GitHub, arXiv.
  Use when: searching the web, 搜一下, 搜小红书, 搜知乎, search GitHub repos,
  find papers, 帮我查, 上网搜, research a topic, multi-platform search,
  read full page content via CDP.
---

# CDP Search

Search 6 platforms from a single script. Returns JSON `[{title, url, snippet, source, extra?}]`.

## Quick Start

```bash
SCRIPT=skills/cdp-search/scripts/cdp_search.py

# Single platform
python3 $SCRIPT -p google -q "query" -l 3

# Multiple platforms (recommended)
python3 $SCRIPT -m google zhihu xiaohongshu -q "query" -l 3

# Read full page content (auto-reuses auth tabs for zhihu/xiaohongshu)
python3 $SCRIPT -r https://zhuanlan.zhihu.com/p/12345
```

## CLI Arguments

| Arg | Short | Description |
|-----|-------|-------------|
| `--platform` | `-p` | Single platform to search |
| `--multi` | `-m` | Multiple platforms (space-separated) |
| `--query` | `-q` | Search query (required for search) |
| `--limit` | `-l` | Max results per platform (default: 3) |
| `--read` | `-r` | URL to read full page content (up to 8000 chars) |

`-p` or `-m` is required for search. `-r` can be used standalone.

## Platforms

| Platform | Method | Auth | Best For |
|----------|--------|------|----------|
| google | CDP (new tab) | No | General, English |
| baidu | CDP (new tab) | No | Chinese general |
| zhihu | CDP (reuse tab) | Logged in | Knowledge, Q&A |
| xiaohongshu | Native MCP binary (stdio) | Logged in | Lifestyle, travel, reviews |
| github | `gh search repos` | Yes (`gh auth`) | Repos, code (sorted by stars) |
| arxiv | Atom API | No | Academic papers |

## Platform Selection by Topic

| Topic | Platforms |
|-------|-----------|
| Life / Travel / Shopping / Food | google + xiaohongshu + zhihu |
| Tech / Programming | google + github + zhihu |
| Academic / Research | google + arxiv + (zhihu if Chinese) |
| Chinese general | baidu + zhihu + xiaohongshu |
| English general | google + github |

GitHub and arXiv queries should be in English. Other platforms accept Chinese.

## Search Pipeline

1. Classify topic → select 2-3 platforms from the table above
2. Generate platform-specific queries (English for GitHub/arXiv, Chinese for others when appropriate)
3. Run `python3 skills/cdp-search/scripts/cdp_search.py -m <platforms> -q "query" -l 3`
4. Parse JSON output — each result has `title`, `url`, `snippet`, `source`, and optional `extra`
5. Evaluate results — if unhelpful, adjust query keywords and retry
6. Cross-validate across sources for consistency
7. Use `-r <url>` for deeper content when a result looks promising (auto-reuses auth tabs for zhihu/xiaohongshu)
8. Fallback: `web_search` + `web_fetch` tools if CDP/API all fail

## Read Page Feature

The `--read` flag fetches full page text (up to 8000 chars) via CDP. It automatically strips headers, footers, navs, sidebars, ads, and comments, then extracts content from semantic elements (`article`, `main`, `.Post-RichText`, `.markdown-body`, etc.).

For auth-required sites (zhihu.com, xiaohongshu.com), it reuses an existing logged-in tab instead of opening a new one.

## Output Format

Search results are printed as JSON array to stdout. Progress info goes to stderr.

```json
[
  {
    "title": "Result title",
    "url": "https://...",
    "snippet": "Brief description...",
    "source": "google",
    "extra": {}  // optional, platform-specific
  }
]
```

Platform-specific `extra` fields:
- **github**: `stars`, `language`, `updated`
- **xiaohongshu**: `likes`, `collected`, `author`

## Key Constraints

- **Chrome**: Must be running with `--remote-debugging-port=9222 --remote-allow-origins=*`.
- **Zhihu**: Must reuse existing logged-in tab. New CDP tabs don't inherit session. If results are empty, user needs to re-login in Chrome.
- **XiaoHongShu**: Connects to MCP HTTP server at `localhost:18060/mcp` (Docker container, v2.0.0). Uses Streamable HTTP MCP: initialize to get session ID, then tools/call with `Mcp-Session-Id` header. Cookies managed by the container.
- **GitHub**: Uses `gh search repos` (authenticated via `gh auth login`, 5000 req/hr). Returns results sorted by stars with fullName, description, stargazersCount, url, language, updatedAt.
- **arXiv**: Pure HTTP API, no CDP needed. Always available.
- **Dependencies**: Python 3.12+, `websocket-client` pip package, `gh` CLI v2.x (for GitHub), `xiaohongshu-mcp` binary (for XiaoHongShu).

## Troubleshooting

- **403 on WebSocket**: Chrome missing `--remote-allow-origins=*` flag.
- **Zhihu empty results**: Tab lost session — re-login in Chrome.
- **XiaoHongShu errors**: Check binary exists at `/tmp/xiaohongshu-mcp-darwin-arm64`, cookies not expired. Re-extract cookies from Chrome if needed.
- **Google/Baidu empty**: Selector changes — check setup-notes.md for current selectors.

See [references/setup-notes.md](references/setup-notes.md) for installation history, CDP pitfalls, selector notes, and login procedures.

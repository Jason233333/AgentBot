#!/usr/bin/env python3
"""CDP Search - Unified search across 6 platforms via Chrome DevTools Protocol."""

import argparse
import json
import sys
import time
import urllib.request
import urllib.parse
import subprocess
import os
import xml.etree.ElementTree as ET
import websocket

CDP_HOST = "http://localhost:9222"

# ---------------------------------------------------------------------------
# CDP helpers
# ---------------------------------------------------------------------------

def cdp_get_pages():
    return json.loads(urllib.request.urlopen(f"{CDP_HOST}/json/list").read())

def cdp_new_tab(url="about:blank"):
    encoded = urllib.parse.quote(url, safe=':/?&=+%#')
    req = urllib.request.Request(f"{CDP_HOST}/json/new?{encoded}", method="PUT")
    return json.loads(urllib.request.urlopen(req).read())

def cdp_close_tab(tab_id):
    try:
        urllib.request.urlopen(urllib.request.Request(f"{CDP_HOST}/json/close/{tab_id}", method="PUT"))
    except Exception:
        pass

def cdp_send(ws, method, params=None, timeout=15):
    msg_id = int(time.time() * 1000) % 1000000
    msg = {"id": msg_id, "method": method}
    if params:
        msg["params"] = params
    ws.send(json.dumps(msg))
    deadline = time.time() + timeout
    while time.time() < deadline:
        ws.settimeout(max(0.1, deadline - time.time()))
        try:
            resp = json.loads(ws.recv())
            if resp.get("id") == msg_id:
                return resp
        except Exception:
            break
    return {}

def cdp_eval(ws, expression, timeout=15):
    """Evaluate JS and return the value."""
    resp = cdp_send(ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True
    }, timeout)
    val = resp.get("result", {}).get("result", {}).get("value", "")
    return val

def find_tab_by_domain(domain):
    """Find an existing tab matching domain."""
    for p in cdp_get_pages():
        if domain in p.get("url", ""):
            return p
    return None

# ---------------------------------------------------------------------------
# Reusable tab strategy:
# - For sites needing auth (zhihu): reuse existing tab, navigate in-place
# - For public sites: open new tab, close after
# ---------------------------------------------------------------------------

def search_with_new_tab(url, js_extract, wait=3):
    """Open new tab, navigate, extract, close."""
    tab = cdp_new_tab("about:blank")
    tab_id = tab["id"]
    try:
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)
        cdp_send(ws, "Page.navigate", {"url": url})
        time.sleep(wait)
        result = cdp_eval(ws, js_extract)
        ws.close()
        return result
    finally:
        cdp_close_tab(tab_id)

def search_with_existing_tab(domain, url, js_extract, wait=4):
    """Reuse existing tab (for auth-required sites), navigate in-place."""
    tab = find_tab_by_domain(domain)
    if not tab:
        # Fallback to new tab
        return search_with_new_tab(url, js_extract, wait)
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)
    cdp_send(ws, "Page.navigate", {"url": url})
    time.sleep(wait)
    result = cdp_eval(ws, js_extract)
    ws.close()
    return result

# ---------------------------------------------------------------------------
# Platform: Google
# ---------------------------------------------------------------------------
def search_google(query, limit=3):
    url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&num={limit+5}&hl=zh-CN"
    js = f"""
    JSON.stringify(
        Array.from(document.querySelectorAll('h3')).slice(0, {limit+3}).map(h3 => {{
            const a = h3.closest('a');
            if (!a) return null;
            const href = a.href;
            if (href.includes('google.com/search')) return null;
            // Find snippet: look in parent container for longer text spans
            let snippet = '';
            const container = a.closest('[data-hveid]') || a.parentElement.parentElement.parentElement;
            if (container) {{
                const allText = Array.from(container.querySelectorAll('span, div'))
                    .filter(el => !el.querySelector('h3') && el.innerText.length > 40 && el.children.length < 3)
                    .map(el => el.innerText);
                if (allText.length) snippet = allText[0];
            }}
            return {{ title: h3.innerText, url: href, snippet: snippet.substring(0, 300) }};
        }}).filter(Boolean).slice(0, {limit})
    )
    """
    raw = search_with_new_tab(url, js, wait=3)
    try:
        return [{**r, "source": "google"} for r in json.loads(raw)[:limit]]
    except Exception:
        return []

# ---------------------------------------------------------------------------
# Platform: Baidu
# ---------------------------------------------------------------------------
def search_baidu(query, limit=3):
    url = f"https://www.baidu.com/s?wd={urllib.parse.quote(query)}"
    js = f"""
    JSON.stringify(
        Array.from(document.querySelectorAll('h3.t, h3[class*="t"]')).slice(0, {limit+3}).map(h3 => {{
            const a = h3.querySelector('a');
            if (!a) return null;
            const container = h3.closest('.c-container') || h3.parentElement.parentElement;
            const snippetEl = container ? container.querySelector('.c-abstract, [class*="content-right"], .c-span-last') : null;
            let snippet = '';
            if (snippetEl) snippet = snippetEl.innerText;
            else if (container) {{
                // Grab text after h3
                const texts = Array.from(container.querySelectorAll('span')).filter(s => s.innerText.length > 30 && !s.querySelector('h3'));
                if (texts.length) snippet = texts[0].innerText;
            }}
            return {{ title: h3.innerText.trim(), url: a.href, snippet: snippet.substring(0, 300) }};
        }}).filter(Boolean).slice(0, {limit})
    )
    """
    raw = search_with_new_tab(url, js, wait=3)
    try:
        return [{**r, "source": "baidu"} for r in json.loads(raw)[:limit]]
    except Exception:
        return []

# ---------------------------------------------------------------------------
# Platform: Zhihu (requires auth - reuse existing tab)
# ---------------------------------------------------------------------------
def search_zhihu(query, limit=3):
    url = f"https://www.zhihu.com/search?type=content&q={urllib.parse.quote(query)}"
    js = f"""
    JSON.stringify(
        Array.from(document.querySelectorAll('h2')).slice(0, {limit+3}).map(h2 => {{
            const a = h2.querySelector('a') || h2.closest('a');
            const text = h2.innerText.trim();
            if (!text || text === '相关搜索') return null;
            let href = '';
            if (a) {{
                href = a.href || '';
                if (href.startsWith('/')) href = 'https://www.zhihu.com' + href;
            }}
            // Snippet from ContentItem
            const card = h2.closest('.List-item, .SearchResult-Card');
            let snippet = '';
            if (card) {{
                const rich = card.querySelector('.RichContent-inner, .SearchItem-content');
                if (rich) snippet = rich.innerText.substring(0, 300);
            }}
            return {{ title: text, url: href, snippet: snippet }};
        }}).filter(Boolean).slice(0, {limit})
    )
    """
    raw = search_with_existing_tab("zhihu.com", url, js, wait=5)
    try:
        return [{**r, "source": "zhihu"} for r in json.loads(raw)[:limit]]
    except Exception:
        return []

# ---------------------------------------------------------------------------
# Platform: GitHub (via gh CLI, authenticated)
# ---------------------------------------------------------------------------
def search_github(query, limit=3):
    try:
        cmd = ["gh", "search", "repos", query, "--limit", str(limit), "--sort", "stars",
               "--json", "fullName,description,stargazersCount,url,language,updatedAt"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            print(f"[github] gh error: {result.stderr[:200]}", file=sys.stderr)
            return []
        items = json.loads(result.stdout)
        results = []
        for item in items[:limit]:
            lang = item.get("language", "") or ""
            results.append({
                "title": item.get("fullName", ""),
                "url": item.get("url", ""),
                "snippet": (item.get("description", "") or "")[:300],
                "source": "github",
                "extra": {
                    "stars": item.get("stargazersCount", 0),
                    "language": lang,
                    "updated": (item.get("updatedAt", "") or "")[:10]
                }
            })
        return results
    except Exception as e:
        print(f"[github] Error: {e}", file=sys.stderr)
        return []

# ---------------------------------------------------------------------------
# Platform: arXiv (HTTP API, no CDP needed)
# ---------------------------------------------------------------------------
def search_arxiv(query, limit=3):
    params = urllib.parse.urlencode({
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": limit,
        "sortBy": "relevance"
    })
    url = f"https://export.arxiv.org/api/query?{params}"
    try:
        data = urllib.request.urlopen(url, timeout=15).read().decode()
        root = ET.fromstring(data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in root.findall("atom:entry", ns)[:limit]:
            title = entry.find("atom:title", ns)
            summary = entry.find("atom:summary", ns)
            link = entry.find("atom:id", ns)
            results.append({
                "title": title.text.strip().replace("\n", " ") if title is not None else "",
                "url": link.text.strip() if link is not None else "",
                "snippet": summary.text.strip()[:300].replace("\n", " ") if summary is not None else "",
                "source": "arxiv"
            })
        return results
    except Exception as e:
        print(f"[arxiv] Error: {e}", file=sys.stderr)
        return []

# ---------------------------------------------------------------------------
# Platform: XiaoHongShu (via mcporter)
# ---------------------------------------------------------------------------
MCPORTER_CWD = "/Users/shingz/Documents/Project/AgentBot/bots/bot-chat/workspace"

def search_xiaohongshu(query, limit=3):
    try:
        cmd = ["mcporter", "call", f'xiaohongshu.search_feeds(keyword: "{query}")']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=MCPORTER_CWD)
        if result.returncode != 0:
            print(f"[xiaohongshu] mcporter error: {result.stderr[:200]}", file=sys.stderr)
            return []
        data = json.loads(result.stdout)
        # mcporter returns: {feeds: [...]} or [{...}] or {feeds: [{feeds: [...]}]}
        if isinstance(data, list):
            # Could be [{feeds: [...]}, ...]
            items = []
            for d in data:
                if isinstance(d, dict) and "feeds" in d:
                    items.extend(d["feeds"])
                else:
                    items.append(d)
        elif isinstance(data, dict):
            items = data.get("feeds", data.get("items", []))
        else:
            items = []
        results = []
        for item in (items or [])[:limit]:
            nc = item.get("noteCard", item.get("note_card", {}))
            title = nc.get("displayTitle", "") or nc.get("display_title", "") or item.get("title", "")
            nid = item.get("id", "") or item.get("note_id", "")
            interact = nc.get("interactInfo", nc.get("interact_info", {}))
            user = nc.get("user", {})
            results.append({
                "title": title,
                "url": f"https://www.xiaohongshu.com/explore/{nid}" if nid else "",
                "snippet": f"likes: {interact.get('likedCount', interact.get('liked_count', ''))}, collected: {interact.get('collectedCount', interact.get('collected_count', ''))}",
                "source": "xiaohongshu",
                "extra": {
                    "likes": interact.get("likedCount", interact.get("liked_count", "")),
                    "collected": interact.get("collectedCount", interact.get("collected_count", "")),
                    "author": user.get("nickname", user.get("nickName", ""))
                }
            })
        return results
    except Exception as e:
        print(f"[xiaohongshu] Error: {e}", file=sys.stderr)
        return []

# ---------------------------------------------------------------------------
# Read full page content via CDP
# ---------------------------------------------------------------------------
def read_page(url):
    js = """
    (function() {
        ['header','footer','nav','.sidebar','.ad','.advertisement','#comments','.Recommendations']
            .forEach(sel => document.querySelectorAll(sel).forEach(el => el.remove()));
        const main = document.querySelector('article, main, .Post-RichText, .RichContent, .content, .article-content, #js_content, .markdown-body, .Post-RichTextContainer');
        return (main || document.body).innerText.substring(0, 8000);
    })()
    """
    # Try reusing existing tab for auth sites
    for domain in ["zhihu.com", "xiaohongshu.com"]:
        if domain in url:
            return search_with_existing_tab(domain, url, js, wait=4)
    return search_with_new_tab(url, js, wait=4)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

PLATFORM_FUNCS = {
    "google": search_google,
    "baidu": search_baidu,
    "zhihu": search_zhihu,
    "github": search_github,
    "arxiv": search_arxiv,
    "xiaohongshu": search_xiaohongshu,
}

def main():
    parser = argparse.ArgumentParser(description="CDP Search")
    parser.add_argument("--platform", "-p", choices=list(PLATFORM_FUNCS.keys()))
    parser.add_argument("--query", "-q", help="Search query")
    parser.add_argument("--limit", "-l", type=int, default=3)
    parser.add_argument("--read", "-r", help="URL to read full content")
    parser.add_argument("--multi", "-m", nargs="+", choices=list(PLATFORM_FUNCS.keys()))
    args = parser.parse_args()

    if args.read:
        print(read_page(args.read))
        return

    if not args.query:
        parser.error("--query is required")

    platforms = args.multi if args.multi else ([args.platform] if args.platform else [])
    if not platforms:
        parser.error("--platform or --multi required")

    all_results = []
    for p in platforms:
        print(f"[Searching {p}...]", file=sys.stderr)
        try:
            results = PLATFORM_FUNCS[p](args.query, args.limit)
            all_results.extend(results)
            print(f"[{p}] {len(results)} results", file=sys.stderr)
        except Exception as e:
            print(f"[{p}] Error: {e}", file=sys.stderr)

    print(json.dumps(all_results, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()

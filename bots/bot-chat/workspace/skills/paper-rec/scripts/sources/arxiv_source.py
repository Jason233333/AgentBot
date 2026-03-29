"""arXiv paper search module using the Atom API."""

import time
import urllib.parse
from typing import Optional

import feedparser
import requests

ARXIV_API_URL = "http://export.arxiv.org/api/query"

DEFAULT_CATEGORIES = ["cs.CL", "cs.AI", "cs.SE", "cs.LG", "cs.MA"]

MAX_RETRIES = 2
RETRY_DELAY = 3  # seconds


def search_arxiv(
    query: str,
    max_results: int = 15,
    categories: Optional[list] = None,
) -> list[dict]:
    """Search arXiv for papers matching the query.

    Args:
        query: Search keywords.
        max_results: Maximum number of results to return.
        categories: List of arXiv category filters (e.g. ["cs.CL", "cs.AI"]).
                    If None, no category filter is applied.

    Returns:
        A list of dicts with keys:
        id, title, authors, abstract, published_date, url, pdf_url, categories, comment
    """
    # Build the search query string
    search_parts = [f"all:{query}"]
    if categories:
        cat_query = " OR ".join(f"cat:{c}" for c in categories)
        search_parts.append(f"({cat_query})")
    search_query = " AND ".join(search_parts)

    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    url = f"{ARXIV_API_URL}?{urllib.parse.urlencode(params)}"

    # Fetch with retries
    xml_content = _fetch_with_retry(url)
    if xml_content is None:
        return []

    # Parse Atom XML via feedparser
    feed = feedparser.parse(xml_content)
    results = []
    for entry in feed.entries:
        paper = _parse_entry(entry)
        results.append(paper)

    print(f"[arXiv] 检索到 {len(results)} 篇论文 (query={query!r})")
    return results


def _fetch_with_retry(url: str) -> Optional[str]:
    """Fetch a URL with up to MAX_RETRIES retries on failure."""
    for attempt in range(1, MAX_RETRIES + 2):  # 1 initial + MAX_RETRIES retries
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            print(f"[arXiv] 请求失败 (第 {attempt} 次): {e}")
            if attempt <= MAX_RETRIES:
                print(f"[arXiv] {RETRY_DELAY} 秒后重试...")
                time.sleep(RETRY_DELAY)
            else:
                print("[arXiv] 已达到最大重试次数，放弃请求。")
    return None


def _parse_entry(entry) -> dict:
    """Parse a single feedparser entry into a standardised dict."""
    # Extract arXiv ID (strip version suffix for canonical form)
    arxiv_id = entry.get("id", "")
    if "abs/" in arxiv_id:
        arxiv_id = arxiv_id.split("abs/")[-1]

    # Authors
    authors = [a.get("name", "") for a in entry.get("authors", [])]

    # Categories / tags
    categories = [t.get("term", "") for t in entry.get("tags", [])]

    # PDF link
    pdf_url = ""
    for link in entry.get("links", []):
        if link.get("type") == "application/pdf" or link.get("title") == "pdf":
            pdf_url = link.get("href", "")
            break
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    # HTML abstract page
    page_url = entry.get("link", "")
    if not page_url and arxiv_id:
        page_url = f"https://arxiv.org/abs/{arxiv_id}"

    # Comment (e.g. "10 pages, 5 figures, accepted at ACL 2025")
    comment = entry.get("arxiv_comment", "")

    return {
        "id": arxiv_id,
        "title": entry.get("title", "").replace("\n", " ").strip(),
        "authors": authors,
        "abstract": entry.get("summary", "").replace("\n", " ").strip(),
        "published_date": entry.get("published", ""),
        "url": page_url,
        "pdf_url": pdf_url,
        "categories": categories,
        "comment": comment,
    }


# ---------------------------------------------------------------------------
# Quick smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    papers = search_arxiv("large language model", max_results=3, categories=["cs.CL"])
    for p in papers:
        print(f"\n  标题: {p['title']}")
        print(f"  作者: {', '.join(p['authors'][:3])}{'...' if len(p['authors']) > 3 else ''}")
        print(f"  日期: {p['published_date']}")
        print(f"  链接: {p['url']}")

"""Paper search via Semantic Scholar API.

Free, no API key required (rate limited to 100 req/5min).
Docs: https://api.semanticscholar.org/api-docs/
"""

import json
import time
import urllib.parse
import urllib.request
import re
from typing import Optional
from datetime import datetime, timedelta

S2_API = "https://api.semanticscholar.org/graph/v1"

FIELDS = "paperId,title,abstract,authors,year,citationCount,url,externalIds,publicationDate,venue,publicationVenue"

MAX_RETRIES = 3
RETRY_DELAY = 8


def search_scholar(
    query: str,
    max_results: int = 10,
    recent_only: bool = True,
    year_from: Optional[int] = None,
) -> list[dict]:
    """Search Semantic Scholar for papers.

    Args:
        query: Search keywords.
        max_results: Max results to return.
        recent_only: If True, restrict to papers from last 2 years.
        year_from: Override start year filter.

    Returns:
        List of dicts compatible with paper_recommender pipeline.
    """
    if year_from is None and recent_only:
        year_from = datetime.now().year - 1

    params = {
        "query": query,
        "limit": min(max_results, 100),
        "fields": FIELDS,
    }
    if year_from:
        params["year"] = f"{year_from}-"

    url = f"{S2_API}/paper/search?{urllib.parse.urlencode(params)}"
    print(f"[Scholar] Searching: {query}")

    data = _fetch_with_retry(url)
    if data is None:
        return []

    try:
        result = json.loads(data)
    except json.JSONDecodeError:
        print("[Scholar] Failed to parse API response")
        return []

    papers = []
    for item in result.get("data", []):
        p = _parse_item(item)
        if p:
            papers.append(p)

    print(f"[Scholar] Got {len(papers)} results")
    return papers


def _fetch_with_retry(url: str) -> Optional[str]:
    """Fetch URL with retries."""
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PaperRecommender/1.0"})
            resp = urllib.request.urlopen(req, timeout=30)
            return resp.read().decode("utf-8")
        except Exception as e:
            print(f"[Scholar] Request failed (attempt {attempt}): {e}")
            if attempt <= MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                print("[Scholar] Max retries reached.")
    return None


def _parse_item(item: dict) -> Optional[dict]:
    """Convert Semantic Scholar result to pipeline-compatible dict."""
    title = item.get("title", "").strip()
    if not title:
        return None

    # Authors
    authors = [a.get("name", "") for a in item.get("authors", []) if a.get("name")]

    # IDs
    ext_ids = item.get("externalIds") or {}
    arxiv_id = ext_ids.get("ArXiv", "")
    s2_id = item.get("paperId", "")

    # URLs
    if arxiv_id:
        paper_url = f"https://arxiv.org/abs/{arxiv_id}"
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
        paper_id = arxiv_id
    else:
        paper_url = item.get("url", f"https://api.semanticscholar.org/{s2_id}")
        pdf_url = ""
        paper_id = s2_id or f"s2:{hash(title) % 10**10}"

    # Date
    pub_date = item.get("publicationDate", "")
    if not pub_date and item.get("year"):
        pub_date = f"{item['year']}-01-01"

    # Citation count
    citations = item.get("citationCount", 0) or 0

    # Venue
    venue = item.get("venue", "")
    pub_venue = item.get("publicationVenue")
    if pub_venue and isinstance(pub_venue, dict):
        venue = pub_venue.get("name", venue)

    # Build comment like arXiv source does
    comment_parts = []
    if venue:
        comment_parts.append(venue)
    if citations > 0:
        comment_parts.append(f"Cited by {citations}")
    comment = ", ".join(comment_parts)

    return {
        "id": paper_id,
        "title": title,
        "authors": authors,
        "abstract": item.get("abstract", "") or "",
        "published_date": pub_date,
        "url": paper_url,
        "pdf_url": pdf_url,
        "categories": [],
        "comment": comment,
        "citations": citations,
        "venue": venue,
        "source": "scholar",
        "upvotes": 0,
    }


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    papers = search_scholar("reinforcement learning policy optimization LLM", max_results=10)
    for i, p in enumerate(papers):
        print(f"\n  {i+1}. [{p.get('citations',0)} cites] {p['title']}")
        print(f"     Authors: {', '.join(p['authors'][:3])}{'...' if len(p['authors'])>3 else ''}")
        print(f"     URL: {p['url']}")
        print(f"     Venue: {p.get('venue','')}")
        if p['abstract']:
            print(f"     Abstract: {p['abstract'][:150]}...")

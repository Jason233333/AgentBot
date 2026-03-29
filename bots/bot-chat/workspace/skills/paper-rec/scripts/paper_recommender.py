#!/usr/bin/env python3
"""Paper Recommender — main entry point.

Workflow:
  1. Determine today's topic group (SWE on odd days, RL on even days)
  2. Fetch papers from arXiv + HuggingFace Daily Papers
  3. Score, deduplicate, and pick the best paper
  4. Fetch the paper's full text (PDF via Jina Reader)
  5. Generate a reading report via Claude API
  6. Publish to WeChat MP via wechat-article-publisher

Usage:
  python3 paper_recommender.py --auto          # full pipeline
  python3 paper_recommender.py --search-only   # search + score only
  python3 paper_recommender.py --dry-run       # search + generate article (no publish)
  python3 paper_recommender.py --paper-id 2603.12345  # generate for specific paper
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure scripts/ is in the path for relative imports
SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent
WORKSPACE_DIR = SKILL_DIR.parent.parent  # bots/bot-chat/workspace

sys.path.insert(0, str(SCRIPTS_DIR))

from sources.arxiv_source import search_arxiv
from sources.hf_daily_source import fetch_hf_daily_papers
from sources.scholar_source import search_scholar
from scorer import score_papers
from dedup import load_published_ids, save_published_id, load_papers_db, save_papers_db, dedup

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("paper-recommender")

# --- Paths ---
CONFIG_PATH = SKILL_DIR / "config.json"
DATA_DIR = SKILL_DIR / "data"
PAPERS_DB_PATH = DATA_DIR / "papers.json"
PUBLISHED_IDS_PATH = DATA_DIR / "published_ids.txt"
TEMPLATE_PATH = SKILL_DIR / "templates" / "paper_explainer.md"
ARTICLES_DIR = WORKSPACE_DIR / "articles"


def load_config() -> dict:
    """Load config.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_today_group(config: dict) -> tuple[str, list[str]]:
    """Determine today's topic group and return (group_name, query_list).

    Odd days -> SWE, even days -> RL.
    Uses Beijing time (UTC+8) for the day calculation.
    """
    bj_time = datetime.now(timezone(timedelta(hours=8)))
    day = bj_time.day
    schedule = config.get("schedule", {})
    group = schedule.get("odd_day", "SWE") if day % 2 == 1 else schedule.get("even_day", "RL")

    # Collect queries for this group
    queries = []
    for topic_cfg in config.get("keywords", {}).values():
        if topic_cfg.get("group") == group:
            queries.extend(topic_cfg.get("queries", []))

    logger.info("Today is day %d (%s) -> group: %s, %d queries",
                day, bj_time.strftime("%Y-%m-%d"), group, len(queries))
    return group, queries


def fetch_all_papers(queries: list[str], config: dict, group: str) -> list[dict]:
    """Fetch papers from all sources.

    Priority: Semantic Scholar (primary) > HuggingFace Daily (trending) > arXiv (fallback).
    """
    all_papers = []
    seen_ids = set()

    def _add_papers(papers: list[dict], source_bonus: float = 0.0):
        """Add papers with dedup by ID."""
        for p in papers:
            pid = p.get("id", "")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                # Normalize fields for scorer
                p.setdefault("summary", p.get("abstract", ""))
                p.setdefault("published", p.get("published_date", ""))
                p.setdefault("comment", p.get("comment", ""))
                p.setdefault("upvotes", 0)
                p.setdefault("citations", 0)
                p["source_bonus"] = source_bonus
                all_papers.append(p)

    # --- Semantic Scholar (primary source) ---
    scholar_cfg = config.get("scholar", {})
    max_per_query = scholar_cfg.get("max_results_per_query", 20)
    for i, q in enumerate(queries):
        logger.info("[Scholar] Searching query %d/%d: %s", i + 1, len(queries), q)
        papers = search_scholar(q, max_results=max_per_query, recent_only=True)
        _add_papers(papers, source_bonus=15.0)  # Scholar results are relevance-ranked
        if i < len(queries) - 1:
            time.sleep(6)  # rate limit: Semantic Scholar 100 req / 5 min
    logger.info("[Scholar] Total: %d unique papers", len(all_papers))

    # --- HuggingFace Daily (trending papers with upvotes) ---
    logger.info("[HuggingFace] Fetching daily papers...")
    try:
        hf_papers = fetch_hf_daily_papers(limit=50)
        kw_cfg = config.get("keywords", {})
        hf_topics = {}
        for topic_name, topic_data in kw_cfg.items():
            if topic_data.get("group") == group:
                hf_topics[topic_name] = topic_data.get("queries", [])
        from sources.hf_daily_source import match_hf_to_keywords
        hf_matched = match_hf_to_keywords(hf_papers, {"topics": hf_topics, "min_keyword_matches": 1})
        before = len(all_papers)
        _add_papers(hf_matched)
        logger.info("[HuggingFace] %d/%d matched, %d new", len(hf_matched), len(hf_papers), len(all_papers) - before)
    except Exception as e:
        logger.warning("[HuggingFace] Failed: %s", e)

    # --- arXiv fallback (only if Scholar returned very few results) ---
    if len(all_papers) < 10:
        logger.info("[arXiv] Scholar returned few results, using arXiv as fallback...")
        arxiv_cfg = config.get("arxiv", {})
        arxiv_max = arxiv_cfg.get("max_results_per_query", 15)
        categories = arxiv_cfg.get("categories", None)
        for i, q in enumerate(queries):
            papers = search_arxiv(q, max_results=arxiv_max, categories=categories)
            _add_papers(papers)
            if i < len(queries) - 1:
                time.sleep(1)
        logger.info("[arXiv fallback] Total now: %d papers", len(all_papers))

    logger.info("Total unique papers: %d", len(all_papers))
    return all_papers


def select_best_paper(papers: list[dict], config: dict) -> dict | None:
    """Score, dedup, and return the best paper.

    Since we now use Semantic Scholar (relevance-ranked), we only need
    basic scoring (citations + recency + keywords) without complex phrase filtering.
    """
    published_ids = load_published_ids(str(PUBLISHED_IDS_PATH))
    logger.info("Already published: %d papers", len(published_ids))

    # Dedup
    unique = dedup(papers, published_ids)
    logger.info("After dedup: %d papers", len(unique))

    if not unique:
        logger.warning("No new papers found after dedup!")
        return None

    # Build scoring config with flattened keywords for keyword_match_score
    scoring_cfg = config.get("scoring", {})
    all_keywords = []
    for topic_cfg in config.get("keywords", {}).values():
        all_keywords.extend(topic_cfg.get("queries", []))
    scoring_cfg["keywords"] = all_keywords

    # Score and sort
    scored = score_papers(unique, scoring_cfg)

    # Filter: require score > 0 and must have abstract (for report generation)
    filtered = [p for p in scored if p.get("score", 0) > 0 and (p.get("abstract") or p.get("summary"))]

    if not filtered:
        logger.warning("No papers with positive score and abstract!")
        return None

    top = filtered[0]
    logger.info("Top paper: [%.1f] %s", top.get("score", 0), top.get("title", "?"))

    return top


def fetch_paper_content(paper: dict) -> str:
    """Fetch paper full text via Jina Reader (PDF or HTML).

    Falls back to abstract if fetching fails.
    """
    pdf_url = paper.get("pdf_url", "")
    page_url = paper.get("url", "")

    # Try PDF via Jina Reader
    for url in [pdf_url, page_url]:
        if not url:
            continue
        jina_url = f"https://r.jina.ai/{url}"
        try:
            logger.info("Fetching paper content via Jina: %s", url)
            resp = requests.get(jina_url, timeout=60, headers={"Accept": "text/plain"})
            if resp.status_code == 200 and len(resp.text) > 500:
                logger.info("Got %d chars of paper content", len(resp.text))
                return resp.text[:50000]  # cap to avoid token overflow
        except requests.RequestException as e:
            logger.warning("Jina fetch failed for %s: %s", url, e)

    # Fallback to abstract
    abstract = paper.get("abstract") or paper.get("summary") or ""
    logger.warning("Using abstract only (%d chars)", len(abstract))
    return abstract


def generate_report(paper: dict, paper_content: str, config: dict) -> str:
    """Generate a reading report using Claude API.

    Returns the Markdown article text.
    """
    # Load prompt template
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    # Fill in template variables
    authors_str = ", ".join(paper.get("authors", [])[:10])
    if len(paper.get("authors", [])) > 10:
        authors_str += " et al."

    prompt = template.format(
        title=paper.get("title", ""),
        authors=authors_str,
        published_date=paper.get("published_date", paper.get("published", "")),
        url=paper.get("url", ""),
        matched_keywords=paper.get("matched_topics", paper.get("categories", "")),
        paper_content=paper_content,
    )

    article_cfg = config.get("article", {})
    min_words = article_cfg.get("min_words", 3000)
    max_words = article_cfg.get("max_words", 5000)

    # Call Claude API
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        # Try reading from nanobot config
        nanobot_config_path = WORKSPACE_DIR.parent.parent.parent / ".nanobot" / ".bot-chat" / "config.json"
        if nanobot_config_path.exists():
            try:
                with open(nanobot_config_path, "r") as f:
                    nb_cfg = json.load(f)
                # providers is a dict: {"anthropic": {"apiKey": "...", ...}, ...}
                providers = nb_cfg.get("providers", {})
                if isinstance(providers, dict):
                    api_key = providers.get("anthropic", {}).get("apiKey", "")
            except Exception:
                pass

    if not api_key:
        logger.error("No ANTHROPIC_API_KEY found. Cannot generate report.")
        return ""

    logger.info("Generating reading report via Claude API...")
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 8000,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt + f"\n\n请确保文章总字数在 {min_words}-{max_words} 字之间。"
                    }
                ],
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        report = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                report += block.get("text", "")
        logger.info("Report generated: %d chars", len(report))
        return report
    except Exception as e:
        logger.error("Claude API call failed: %s", e)
        return ""


def publish_to_wechat(article_md: str, paper: dict, config: dict) -> bool:
    """Save article to workspace/articles/ and publish via wechat-article-publisher."""
    wechat_cfg = config.get("wechat", {})
    publish_script = WORKSPACE_DIR / wechat_cfg.get("publish_script", "skills/wechat-article-publisher/scripts/publish_wechat.py")
    wechat_config = WORKSPACE_DIR / wechat_cfg.get("config_path", "skills/wechat-article-publisher/config.json")
    template = wechat_cfg.get("template", "standard")

    # Save article to file
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    safe_title = paper.get("title", "paper")[:50].replace(" ", "-").replace("/", "-")
    article_path = ARTICLES_DIR / f"{today}-{safe_title}.md"

    with open(article_path, "w", encoding="utf-8") as f:
        f.write(article_md)
    logger.info("Article saved to %s", article_path)

    # Publish via script
    cmd = [
        sys.executable, str(publish_script),
        str(article_path),
        "--config", str(wechat_config),
        "--template", template,
    ]
    logger.info("Publishing: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(WORKSPACE_DIR))
        if result.returncode == 0:
            logger.info("Published successfully!")
            logger.info(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
            return True
        else:
            logger.error("Publish failed (exit %d): %s", result.returncode, result.stderr[-500:])
            return False
    except Exception as e:
        logger.error("Publish error: %s", e)
        return False


def record_paper(paper: dict, published: bool) -> None:
    """Record paper to the tracking database."""
    papers_db = load_papers_db(str(PAPERS_DB_PATH))
    record = {
        "id": paper.get("id"),
        "title": paper.get("title"),
        "score": paper.get("score", 0),
        "published": published,
        "published_date": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "url": paper.get("url", ""),
    }
    papers_db.append(record)
    save_papers_db(str(PAPERS_DB_PATH), papers_db)

    if published:
        save_published_id(str(PUBLISHED_IDS_PATH), paper["id"])
    logger.info("Paper recorded: %s (published=%s)", paper.get("id"), published)


def run_pipeline(args) -> None:
    """Main pipeline."""
    config = load_config()
    group, queries = get_today_group(config)

    if not queries:
        logger.error("No queries for group '%s'. Check config.json.", group)
        return

    # --- Step 1: Fetch ---
    logger.info("=== Step 1: Fetching papers ===")
    all_papers = fetch_all_papers(queries, config, group)

    if not all_papers:
        logger.error("No papers fetched from any source.")
        return

    # --- Step 2: Select best ---
    logger.info("=== Step 2: Selecting best paper ===")
    best = select_best_paper(all_papers, config)
    if not best:
        logger.error("No suitable paper found.")
        return

    logger.info("Selected: [%.1f] %s", best.get("score", 0), best.get("title", "?"))
    logger.info("URL: %s", best.get("url", ""))

    if args.search_only:
        # Print top 10 for review
        published_ids = load_published_ids(str(PUBLISHED_IDS_PATH))
        unique = dedup(all_papers, published_ids)
        scoring_cfg = config.get("scoring", {})
        all_kw = []
        for t in config.get("keywords", {}).values():
            all_kw.extend(t.get("queries", []))
        scoring_cfg["keywords"] = all_kw
        scored = score_papers(unique, scoring_cfg)
        filtered = [p for p in scored if p.get("score", 0) > 0]
        if not filtered:
            filtered = scored[:10]
        print(f"\n=== Top 10 Papers (group: {group}) ===")
        for i, p in enumerate(filtered[:10]):
            cites = p.get('citations', 0)
            src = p.get('source', 'arxiv')
            print(f"  {i+1}. [{p.get('score', 0):.1f}] {p.get('title', '?')}")
            print(f"     {p.get('url', '')}")
            print(f"     src={src}, citations={cites}, {p.get('comment', '')}")
        return

    # --- Step 3: Fetch full text ---
    logger.info("=== Step 3: Fetching paper content ===")
    content = fetch_paper_content(best)

    # --- Step 4: Generate report ---
    logger.info("=== Step 4: Generating reading report ===")
    report = generate_report(best, content, config)
    if not report:
        logger.error("Failed to generate report.")
        return

    if args.dry_run:
        # Save article but don't publish
        ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        safe_title = best.get("title", "paper")[:50].replace(" ", "-").replace("/", "-")
        path = ARTICLES_DIR / f"{today}-{safe_title}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\nArticle saved to: {path}")
        print(f"Title: {best.get('title')}")
        print(f"Score: {best.get('score', 0)}")
        record_paper(best, published=False)
        return

    # --- Step 5: Publish ---
    logger.info("=== Step 5: Publishing to WeChat ===")
    ok = publish_to_wechat(report, best, config)
    record_paper(best, published=ok)

    if ok:
        logger.info("Pipeline completed successfully!")
    else:
        logger.warning("Pipeline completed but publish failed. Article saved locally.")


def run_specific_paper(paper_id: str) -> None:
    """Generate a report for a specific arXiv paper."""
    config = load_config()
    logger.info("Fetching specific paper: %s", paper_id)

    # Build a minimal paper dict
    paper = {
        "id": paper_id,
        "url": f"https://arxiv.org/abs/{paper_id}",
        "pdf_url": f"https://arxiv.org/pdf/{paper_id}",
        "title": paper_id,
        "authors": [],
        "published_date": "",
    }

    # Try to get metadata from arXiv
    try:
        results = search_arxiv(f"id:{paper_id}", max_results=1)
        if results:
            paper.update(results[0])
    except Exception as e:
        logger.warning("Could not fetch arXiv metadata: %s", e)

    content = fetch_paper_content(paper)
    report = generate_report(paper, content, config)
    if report:
        ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        safe_title = paper.get("title", "paper")[:50].replace(" ", "-").replace("/", "-")
        path = ARTICLES_DIR / f"{today}-{safe_title}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Article saved to: {path}")
    else:
        logger.error("Failed to generate report.")


def main():
    parser = argparse.ArgumentParser(description="Paper Recommender")
    parser.add_argument("--auto", action="store_true", help="Full pipeline: search + generate + publish")
    parser.add_argument("--search-only", action="store_true", help="Search and score only")
    parser.add_argument("--dry-run", action="store_true", help="Search + generate, no publish")
    parser.add_argument("--paper-id", type=str, help="Generate report for specific arXiv paper ID")
    args = parser.parse_args()

    if args.paper_id:
        run_specific_paper(args.paper_id)
    elif args.auto or args.search_only or args.dry_run:
        run_pipeline(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

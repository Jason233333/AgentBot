#!/usr/bin/env python3
"""Search and score RL papers from arXiv + Semantic Scholar + HuggingFace."""

from __future__ import annotations

import argparse
import datetime
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    requests = None

ARXIV_API = "https://export.arxiv.org/api/query"
S2_API = "https://api.semanticscholar.org/graph/v1/paper"
S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
HF_PAPERS = "https://huggingface.co/api/daily_papers"

TOP_VENUES = {
    "neurips", "nips", "icml", "iclr", "acl", "emnlp", "aaai",
    "cvpr", "iccv", "eccv", "naacl", "coling", "ijcai",
}

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "Agentic RL": ["agentic reinforcement learning", "agent RL", "LLM agent reinforcement"],
    "RL Training / RL Infrastructure": ["reinforcement learning training", "RL infrastructure", "distributed RL", "RL framework"],
    "RL + Code Generation / SWE Agent": ["reinforcement learning code", "SWE agent", "code generation RL", "software engineering agent"],
    "Multi-Agent RL": ["multi-agent reinforcement learning", "MARL", "cooperative RL"],
    "RLHF / RLAIF / Reward Modeling": ["RLHF", "RLAIF", "reward model", "human feedback reinforcement"],
    "Offline RL / Model-based RL": ["offline reinforcement learning", "model-based RL", "batch RL"],
    "RL for LLM Reasoning": ["reinforcement learning reasoning", "RL LLM reasoning", "chain of thought RL"],
}

DEFAULT_WEIGHTS = {
    "w1_venue": 0.25,
    "w2_citation": 0.15,
    "w3_recency": 0.25,
    "w4_hf_upvotes": 0.15,
    "w5_github_stars": 0.10,
    "w6_social_buzz": 0.10,
}

DEFAULT_CATEGORIES = ["cs.LG", "cs.AI", "cs.CL", "cs.SE", "cs.MA"]


def build_arxiv_query(topic: str, categories: list[str] | None = None) -> str:
    cats = categories or DEFAULT_CATEGORIES
    keywords = TOPIC_KEYWORDS.get(topic, [topic.lower()])
    kw_part = " OR ".join(f'all:"{kw}"' for kw in keywords)
    cat_part = " OR ".join(f"cat:{c}" for c in cats)
    return f"({kw_part}) AND ({cat_part})"


def search_arxiv(topic: str, max_results: int = 30, days_back: int = 180) -> list[dict[str, Any]]:
    query = build_arxiv_query(topic)
    params = urllib.parse.urlencode({
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })
    url = f"{ARXIV_API}?{params}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        xml_data = resp.read()

    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(xml_data)
    papers = []
    cutoff = datetime.date.today() - datetime.timedelta(days=days_back)

    for entry in root.findall("atom:entry", ns):
        arxiv_id_raw = entry.find("atom:id", ns)
        if arxiv_id_raw is None:
            continue
        arxiv_id = arxiv_id_raw.text.strip().split("/abs/")[-1]
        if "v" in arxiv_id:
            arxiv_id = arxiv_id.rsplit("v", 1)[0]

        published_raw = entry.find("atom:published", ns)
        if published_raw is None:
            continue
        published_str = published_raw.text.strip()[:10]
        try:
            pub_date = datetime.date.fromisoformat(published_str)
        except ValueError:
            continue
        if pub_date < cutoff:
            continue

        title_el = entry.find("atom:title", ns)
        abstract_el = entry.find("atom:summary", ns)
        title = " ".join((title_el.text or "").split()) if title_el is not None else ""
        abstract = " ".join((abstract_el.text or "").split()) if abstract_el is not None else ""

        authors = []
        for author_el in entry.findall("atom:author", ns):
            name_el = author_el.find("atom:name", ns)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        pdf_url = ""
        for link in entry.findall("atom:link", ns):
            if link.get("title") == "pdf":
                pdf_url = link.get("href", "")
                break

        categories = []
        for cat in entry.findall("atom:category", ns):
            term = cat.get("term", "")
            if term:
                categories.append(term)

        papers.append({
            "arxiv_id": arxiv_id,
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "published_date": published_str,
            "pdf_url": pdf_url,
            "categories": categories,
            "venue": "",
            "citation_count": 0,
            "hf_upvotes": 0,
            "github_stars": 0,
            "social_buzz": 0,
        })

    return papers


def enrich_with_s2(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich papers with Semantic Scholar data (citations, venue)."""
    if not requests:
        return papers
    for paper in papers:
        try:
            resp = requests.get(
                f"{S2_API}/ARXIV:{paper['arxiv_id']}",
                params={"fields": "citationCount,influentialCitationCount,venue,externalIds,openAccessPdf"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                paper["citation_count"] = data.get("citationCount", 0) or 0
                paper["venue"] = data.get("venue", "") or ""
            time.sleep(0.5)
        except Exception:
            continue
    return papers


def enrich_with_hf(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check HuggingFace daily papers for upvote counts."""
    if not requests:
        return papers
    try:
        resp = requests.get(HF_PAPERS, timeout=10)
        if resp.status_code != 200:
            return papers
        hf_data = resp.json()
        hf_map: dict[str, int] = {}
        for item in hf_data:
            paper_data = item.get("paper", {})
            hf_id = paper_data.get("id", "")
            upvotes = paper_data.get("upvotes", 0)
            if hf_id:
                hf_map[hf_id] = upvotes
        for paper in papers:
            paper["hf_upvotes"] = hf_map.get(paper["arxiv_id"], 0)
    except Exception:
        pass
    return papers


def compute_score(paper: dict[str, Any], weights: dict[str, float] | None = None) -> float:
    w = weights or DEFAULT_WEIGHTS

    venue = (paper.get("venue") or "").lower().strip()
    venue_bonus = 1.0 if any(v in venue for v in TOP_VENUES) else 0.0

    citations = paper.get("citation_count", 0) or 0
    try:
        pub_date = datetime.date.fromisoformat(paper.get("published_date", "2020-01-01"))
    except ValueError:
        pub_date = datetime.date(2020, 1, 1)
    age_months = max(1, (datetime.date.today() - pub_date).days / 30.0)
    cit_per_month = citations / age_months
    citation_score = min(1.0, math.log1p(cit_per_month) / math.log1p(50))

    age_days = (datetime.date.today() - pub_date).days
    recency_score = max(0.0, 1.0 - age_days / 180.0)

    hf = paper.get("hf_upvotes", 0) or 0
    hf_score = min(1.0, math.log1p(hf) / math.log1p(100))

    stars = paper.get("github_stars", 0) or 0
    stars_score = min(1.0, math.log1p(stars) / math.log1p(500))

    buzz = paper.get("social_buzz", 0) or 0
    buzz_score = min(1.0, math.log1p(buzz) / math.log1p(50))

    score = (
        w["w1_venue"] * venue_bonus
        + w["w2_citation"] * citation_score
        + w["w3_recency"] * recency_score
        + w["w4_hf_upvotes"] * hf_score
        + w["w5_github_stars"] * stars_score
        + w["w6_social_buzz"] * buzz_score
    )

    paper["score"] = round(score, 4)
    paper["score_breakdown"] = {
        "venue_bonus": round(venue_bonus, 3),
        "citation_score": round(citation_score, 3),
        "recency_score": round(recency_score, 3),
        "hf_score": round(hf_score, 3),
        "stars_score": round(stars_score, 3),
        "buzz_score": round(buzz_score, 3),
    }
    return score


def filter_recommended(papers: list[dict[str, Any]], exclude_ids: set[str]) -> list[dict[str, Any]]:
    return [p for p in papers if p["arxiv_id"] not in exclude_ids]


def load_exclude_ids(path: str) -> set[str]:
    exclude = set()
    p = Path(path)
    if not p.exists():
        return exclude
    for line in p.read_text(encoding="utf-8").strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            if "arxiv_id" in record:
                exclude.add(record["arxiv_id"])
        except json.JSONDecodeError:
            continue
    return exclude


def parse_weights_from_topics(topics_path: str) -> dict[str, float]:
    """Parse scoring weights from topics.md if available."""
    p = Path(topics_path)
    if not p.exists():
        return DEFAULT_WEIGHTS
    content = p.read_text(encoding="utf-8")
    weights = dict(DEFAULT_WEIGHTS)
    for key in DEFAULT_WEIGHTS:
        match = re.search(rf"^{key}:\s*([\d.]+)", content, re.MULTILINE)
        if match:
            weights[key] = float(match.group(1))
    return weights


def main() -> None:
    parser = argparse.ArgumentParser(description="Search and score RL papers")
    parser.add_argument("--topic", required=True, help="Topic to search for")
    parser.add_argument("--exclude", default="", help="Path to recommended.jsonl for dedup")
    parser.add_argument("--top", type=int, default=1, help="Number of top papers to return")
    parser.add_argument("--max-results", type=int, default=30, help="Max arXiv results to fetch")
    parser.add_argument("--days-back", type=int, default=180, help="Search window in days")
    parser.add_argument("--topics-file", default="", help="Path to topics.md for weights")
    parser.add_argument("--skip-enrich", action="store_true", help="Skip S2/HF enrichment (for testing)")
    args = parser.parse_args()

    weights = parse_weights_from_topics(args.topics_file) if args.topics_file else DEFAULT_WEIGHTS
    exclude_ids = load_exclude_ids(args.exclude) if args.exclude else set()

    papers = search_arxiv(args.topic, max_results=args.max_results, days_back=args.days_back)

    if not args.skip_enrich:
        papers = enrich_with_s2(papers)
        papers = enrich_with_hf(papers)

    for p in papers:
        compute_score(p, weights)

    papers = filter_recommended(papers, exclude_ids)
    papers.sort(key=lambda x: x.get("score", 0), reverse=True)
    top_papers = papers[: args.top]

    print(json.dumps(top_papers, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

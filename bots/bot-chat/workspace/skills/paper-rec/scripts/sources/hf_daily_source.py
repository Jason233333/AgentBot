"""HuggingFace Daily Papers data source module.

Fetches trending papers from the HuggingFace Daily Papers API
and provides keyword-based filtering for topic matching.
"""

import re
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

HF_DAILY_API = "https://huggingface.co/api/daily_papers"


def fetch_hf_daily_papers(limit: int = 50) -> list[dict]:
    """Fetch daily trending papers from HuggingFace.

    Args:
        limit: Maximum number of papers to return.

    Returns:
        List of paper dicts with keys:
            id, title, authors, abstract, published_date, url, upvotes
    """
    try:
        resp = requests.get(HF_DAILY_API, timeout=30)
        resp.raise_for_status()
        raw_papers = resp.json()
    except requests.exceptions.RequestException as e:
        logger.error("Failed to fetch HuggingFace daily papers: %s", e)
        return []
    except ValueError as e:
        logger.error("Failed to parse HuggingFace API response: %s", e)
        return []

    papers = []
    for entry in raw_papers[:limit]:
        paper_data = entry.get("paper", {})

        # Extract arXiv ID from the paper id field
        arxiv_id = _extract_arxiv_id(paper_data.get("id", ""))
        if not arxiv_id:
            continue

        authors = [
            a.get("name", "") if isinstance(a, dict) else str(a)
            for a in paper_data.get("authors", [])
        ]

        papers.append({
            "id": arxiv_id,
            "title": paper_data.get("title", "").strip(),
            "authors": authors,
            "abstract": paper_data.get("summary", "").strip(),
            "published_date": paper_data.get("publishedAt", ""),
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "upvotes": entry.get("paper", {}).get("upvotes", 0),
        })

    logger.info("Fetched %d papers from HuggingFace Daily Papers", len(papers))
    return papers


def _extract_arxiv_id(paper_id: str) -> Optional[str]:
    """Extract arXiv ID from a HuggingFace paper ID.

    The HF API typically uses the raw arXiv ID (e.g. '2405.12345')
    as the paper identifier. This function normalizes it.
    """
    if not paper_id:
        return None

    # Match standard arXiv ID patterns: YYMM.NNNNN or category/YYMMNNN
    match = re.search(r"(\d{4}\.\d{4,5})", paper_id)
    if match:
        return match.group(1)

    match = re.search(r"([a-z\-]+/\d{7})", paper_id)
    if match:
        return match.group(1)

    # Fall back to using the raw ID if it looks reasonable
    if re.match(r"^[\w.\-/]+$", paper_id):
        return paper_id

    return None


def match_hf_to_keywords(hf_papers: list, keywords_config: dict) -> list[dict]:
    """Filter papers by keyword matching against title and abstract.

    Args:
        hf_papers: List of paper dicts from fetch_hf_daily_papers().
        keywords_config: Dict with structure:
            {
                "topics": {
                    "topic_name": ["keyword1", "keyword2", ...],
                    ...
                },
                "min_keyword_matches": 1  # optional, default 1
            }

    Returns:
        List of paper dicts, each augmented with a "matched_topics" field.
    """
    topics = keywords_config.get("topics", {})
    min_matches = keywords_config.get("min_keyword_matches", 2)

    if not topics:
        logger.warning("No keyword topics configured; returning all papers")
        return hf_papers

    matched_papers = []

    # Common stop words to ignore when splitting query phrases
    stop_words = {"a", "an", "the", "of", "for", "in", "on", "and", "or", "with", "to", "from"}

    for paper in hf_papers:
        text = f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()
        matched_topics = []

        for topic, keywords in topics.items():
            # Split query phrases into individual words for flexible matching
            all_words = set()
            for kw in keywords:
                all_words.update(w.lower() for w in kw.split() if len(w) > 2)
            # Count how many unique keyword words appear in the text
            hit_count = sum(1 for w in all_words if w in text)
            if hit_count >= min_matches:
                matched_topics.append(topic)

        if matched_topics:
            enriched = {**paper, "matched_topics": matched_topics}
            matched_papers.append(enriched)

    logger.info(
        "Keyword filtering: %d/%d papers matched",
        len(matched_papers),
        len(hf_papers),
    )
    return matched_papers


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    papers = fetch_hf_daily_papers(limit=10)
    for p in papers:
        print(f"[{p['id']}] ({p['upvotes']} upvotes) {p['title']}")

    # Example keyword filtering
    example_config = {
        "topics": {
            "llm": ["language model", "llm", "gpt", "transformer"],
            "diffusion": ["diffusion", "stable diffusion", "image generation"],
            "rl": ["reinforcement learning", "rlhf", "reward model"],
        },
        "min_keyword_matches": 1,
    }
    filtered = match_hf_to_keywords(papers, example_config)
    print(f"\nFiltered: {len(filtered)} papers")
    for p in filtered:
        print(f"  [{p['id']}] topics={p['matched_topics']} - {p['title']}")

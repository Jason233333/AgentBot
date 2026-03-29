#!/usr/bin/env python3
"""Paper scoring module.

Scores papers on a 0-100 scale across four dimensions:
- venue_score (30): top venue detection from comment field
- hf_upvotes (25): HuggingFace community upvotes
- recency_score (20): publication freshness
- keyword_match_score (25): keyword relevance in title + abstract
"""

import re
from datetime import datetime, timezone

# Top-tier ML/NLP/AI venues
TOP_VENUES = [
    "ICLR", "ICML", "NeurIPS", "NIPS",
    "ACL", "EMNLP", "NAACL",
    "AAAI", "IJCAI",
    "CVPR", "ICCV", "ECCV",
    "KDD", "SIGIR", "WWW",
    "COLM", "JMLR", "TMLR",
]

# Pattern to detect workshop papers
WORKSHOP_PATTERN = re.compile(r"\bworkshop\b", re.IGNORECASE)


def _build_venue_pattern():
    """Build a compiled regex matching any top venue name."""
    escaped = [re.escape(v) for v in TOP_VENUES]
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)


VENUE_PATTERN = _build_venue_pattern()


def _venue_score(paper: dict, max_score: float = 20.0, workshop_score: float = 8.0) -> float:
    """Score based on venue mentions in the comment field.

    - Top venue match: max_score points
    - Workshop match (no top venue): workshop_score points
    - Otherwise: 0
    """
    comment = paper.get("comment") or ""
    if VENUE_PATTERN.search(comment):
        return max_score
    if WORKSHOP_PATTERN.search(comment):
        return workshop_score
    return 0.0


def _hf_upvotes_score(paper: dict, max_score: float = 25.0, threshold: int = 20) -> float:
    """Score based on HuggingFace upvotes.

    Linear scaling: 0 upvotes -> 0, >= threshold -> max_score.
    """
    upvotes = paper.get("upvotes", 0) or 0
    if upvotes <= 0:
        return 0.0
    return min(upvotes / threshold, 1.0) * max_score


def _recency_score(paper: dict, max_score: float = 20.0,
                   full_score_days: int = 30, decay_days: int = 365) -> float:
    """Score based on publication recency.

    - Within full_score_days: full max_score
    - Between full_score_days and decay_days: linear decay to 0
    - Beyond decay_days: 0
    """
    published = paper.get("published") or paper.get("publishedAt") or paper.get("date")
    if not published:
        return 0.0

    # Parse date string or use directly if already datetime
    if isinstance(published, str):
        # Handle common formats: ISO 8601, date-only
        published = published.rstrip("Z").split("T")[0] if "T" in published else published.split(" ")[0]
        try:
            pub_date = datetime.strptime(published, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return 0.0
    elif isinstance(published, datetime):
        pub_date = published if published.tzinfo else published.replace(tzinfo=timezone.utc)
    else:
        return 0.0

    now = datetime.now(timezone.utc)
    age_days = (now - pub_date).days

    if age_days <= full_score_days:
        return max_score
    if age_days >= decay_days:
        return 0.0
    # Linear decay
    return max_score * (decay_days - age_days) / (decay_days - full_score_days)


def _keyword_match_score(paper: dict, keywords: list, max_score: float = 25.0) -> float:
    """Score based on keyword matches in title + abstract.

    Uses full phrase matching. Each unique keyword phrase matched contributes
    equally; normalized to max_score. Requires at least 1 match to score > 0.
    """
    if not keywords:
        return 0.0

    title = (paper.get("title", "") or "").lower()
    abstract = (paper.get("summary", "") or paper.get("abstract", "") or "").lower()
    text = f"{title} {abstract}"

    matched = sum(1 for kw in keywords if kw.lower() in text)
    if matched == 0:
        return 0.0
    # Bonus: title match is worth more
    title_matched = sum(1 for kw in keywords if kw.lower() in title)
    title_bonus = min(title_matched * 0.1, 0.3) * max_score

    base = min(matched / max(len(keywords), 1), 1.0) * max_score
    return min(base + title_bonus, max_score)


def _citation_score(paper: dict, max_score: float = 15.0, cap: int = 100) -> float:
    """Score based on citation count.

    Log-scaled: 0 cites -> 0, >= cap -> max_score.
    """
    import math
    citations = paper.get("citations", 0) or 0
    if citations <= 0:
        return 0.0
    # Log scale: log(1+citations)/log(1+cap) * max_score
    return min(math.log(1 + citations) / math.log(1 + cap), 1.0) * max_score


def score_paper(paper: dict, config: dict) -> float:
    """Compute a composite score (0-100) for a single paper.

    Args:
        paper: Paper dict with fields like title, summary, comment, upvotes, published.
        config: Scoring config dict (from config.json "scoring" section).
                Expected keys: keywords (list[str]).
                Optional keys: weights (dict with venue/hf/recency/keyword overrides),
                               hf_upvote_threshold (int), recency_full_days (int),
                               recency_decay_days (int).

    Returns:
        Float score clamped to [0, 100].
    """
    keywords = config.get("keywords", [])
    weights = config.get("weights", {})
    hf_threshold = config.get("hf_upvote_threshold", 20)
    recency_full = config.get("recency_full_days", 30)
    recency_decay = config.get("recency_decay_days", 365)

    # Compute each dimension with configurable max scores
    venue_max = weights.get("venue", 15.0)
    workshop_max = weights.get("workshop", 6.0)
    v = _venue_score(paper, max_score=venue_max, workshop_score=workshop_max)
    h = _hf_upvotes_score(paper, max_score=weights.get("hf", 20.0), threshold=hf_threshold)
    r = _recency_score(paper, max_score=weights.get("recency", 20.0),
                       full_score_days=recency_full, decay_days=recency_decay)
    k = _keyword_match_score(paper, keywords, max_score=weights.get("keyword", 30.0))
    c = _citation_score(paper, max_score=weights.get("citation", 15.0),
                        cap=config.get("citation_cap", 100))

    # Source bonus: Scholar results get a relevance bonus since they're semantically matched
    sb = paper.get("source_bonus", 0.0)

    total = v + h + r + k + c + sb
    return round(min(max(total, 0.0), 100.0), 2)


def score_papers(papers: list, config: dict) -> list:
    """Score a list of papers and return them sorted by score (descending).

    Each paper dict gets a 'score' field added.

    Args:
        papers: List of paper dicts.
        config: Scoring config dict.

    Returns:
        List of paper dicts sorted by score descending.
    """
    for p in papers:
        p["score"] = score_paper(p, config)
    return sorted(papers, key=lambda x: x["score"], reverse=True)


if __name__ == "__main__":
    # Quick smoke test
    sample = {
        "title": "Scaling Language Models with Mixture of Experts",
        "summary": "We propose a novel agent framework for reasoning and tool use.",
        "comment": "Accepted at ICML 2025",
        "upvotes": 35,
        "published": "2025-03-15",
    }
    test_config = {
        "keywords": ["agent", "reasoning", "LLM", "tool use", "scaling"],
    }
    s = score_paper(sample, test_config)
    print(f"Score: {s}")
    # Expected: venue=30 + hf=25 + recency=20 + keyword ~= 20 -> ~95

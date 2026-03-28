# Paper Daily Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a nanobot skill that searches RL papers daily, generates deep interpretations, and publishes to WeChat MP.

**Architecture:** A single `paper-daily` skill with 3 Python scripts (search, figure extraction, LaTeX rendering), reference docs for topic config and writing style, and a Markdown template. Reuses `wechat-article-publisher` from ClawHub for publishing. Cron triggers agent twice daily.

**Tech Stack:** Python (arxiv, requests, pymupdf, matplotlib, beautifulsoup4, Pillow), nanobot skill framework, wechat-article-publisher skill, nanobot cron.

---

### Task 1: Initialize Skill Skeleton

**Files:**
- Create: `bots/bot-chat/workspace/skills/paper-daily/SKILL.md`
- Create: `bots/bot-chat/workspace/skills/paper-daily/scripts/` (dir)
- Create: `bots/bot-chat/workspace/skills/paper-daily/references/` (dir)
- Create: `bots/bot-chat/workspace/skills/paper-daily/assets/templates/` (dir)
- Create: `bots/bot-chat/workspace/skills/paper-daily/scripts/requirements.txt`

**Step 1: Run init_skill.py**

```bash
cd /Users/shingz/Documents/Project/AgentBot
python nanobot/skills/skill-creator/scripts/init_skill.py paper-daily \
  --path bots/bot-chat/workspace/skills \
  --resources scripts,references,assets
```

**Step 2: Create requirements.txt**

Write to `bots/bot-chat/workspace/skills/paper-daily/scripts/requirements.txt`:

```
arxiv>=2.1.0
requests>=2.31.0
pymupdf>=1.24.0
matplotlib>=3.8.0
Pillow>=10.0.0
beautifulsoup4>=4.12.0
lxml>=5.0.0
```

**Step 3: Install dependencies**

```bash
pip install -r bots/bot-chat/workspace/skills/paper-daily/scripts/requirements.txt
```

**Step 4: Create assets/templates directory**

```bash
mkdir -p bots/bot-chat/workspace/skills/paper-daily/assets/templates
```

**Step 5: Commit**

```bash
git add bots/bot-chat/workspace/skills/paper-daily/
git commit -m "feat(paper-daily): initialize skill skeleton"
```

---

### Task 2: Write search_papers.py — arXiv + Semantic Scholar Search

**Files:**
- Create: `bots/bot-chat/workspace/skills/paper-daily/scripts/search_papers.py`
- Create: `tests/skills/paper-daily/test_search_papers.py`

**Step 1: Write the test**

Write to `tests/skills/paper-daily/test_search_papers.py`:

```python
"""Tests for paper search and scoring."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "bots/bot-chat/workspace/skills/paper-daily/scripts/search_papers.py"

# Import the module for unit tests
sys.path.insert(0, str(SCRIPT.parent))


class TestArxivSearch:
    """Test arXiv API querying."""

    def test_build_arxiv_query_single_topic(self):
        from search_papers import build_arxiv_query
        q = build_arxiv_query("Agentic RL", categories=["cs.LG", "cs.AI"])
        assert "Agentic RL" in q or "agentic" in q.lower()
        assert "cs.LG" in q

    def test_build_arxiv_query_with_keywords(self):
        from search_papers import build_arxiv_query
        q = build_arxiv_query("RL + Code Generation", categories=["cs.LG"])
        assert "cs.LG" in q


class TestScoring:
    """Test dual-channel scoring logic."""

    def test_venue_bonus_neurips(self):
        from search_papers import compute_score
        paper = {
            "venue": "NeurIPS",
            "citation_count": 50,
            "published_date": "2025-12-01",
            "hf_upvotes": 0,
            "github_stars": 0,
            "social_buzz": 0,
        }
        weights = {
            "w1_venue": 0.25, "w2_citation": 0.15, "w3_recency": 0.25,
            "w4_hf_upvotes": 0.15, "w5_github_stars": 0.10, "w6_social_buzz": 0.10,
        }
        score = compute_score(paper, weights)
        assert score > 0

    def test_fresh_paper_scores_via_recency(self):
        from search_papers import compute_score
        import datetime
        today = datetime.date.today().isoformat()
        paper = {
            "venue": "",
            "citation_count": 0,
            "published_date": today,
            "hf_upvotes": 30,
            "github_stars": 100,
            "social_buzz": 5,
        }
        weights = {
            "w1_venue": 0.25, "w2_citation": 0.15, "w3_recency": 0.25,
            "w4_hf_upvotes": 0.15, "w5_github_stars": 0.10, "w6_social_buzz": 0.10,
        }
        score = compute_score(paper, weights)
        assert score > 0.3  # Fresh + community signals should produce decent score


class TestDedup:
    """Test deduplication against recommended.jsonl."""

    def test_filter_excludes_known_ids(self):
        from search_papers import filter_recommended
        papers = [
            {"arxiv_id": "2403.11111", "title": "Paper A"},
            {"arxiv_id": "2403.22222", "title": "Paper B"},
            {"arxiv_id": "2403.33333", "title": "Paper C"},
        ]
        exclude_ids = {"2403.22222"}
        result = filter_recommended(papers, exclude_ids)
        assert len(result) == 2
        assert all(p["arxiv_id"] != "2403.22222" for p in result)


class TestCLI:
    """Test CLI interface."""

    def test_help_flag(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "--topic" in result.stdout
```

**Step 2: Run test to verify it fails**

```bash
cd /Users/shingz/Documents/Project/AgentBot
python -m pytest tests/skills/paper-daily/test_search_papers.py -v
```

Expected: FAIL — `search_papers` module not found.

**Step 3: Implement search_papers.py**

Write to `bots/bot-chat/workspace/skills/paper-daily/scripts/search_papers.py`:

```python
#!/usr/bin/env python3
"""Search and score RL papers from arXiv + Semantic Scholar + HuggingFace."""

from __future__ import annotations

import argparse
import datetime
import json
import math
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
        # Strip version suffix
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
            time.sleep(0.5)  # Rate limit
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
            upvotes = item.get("paper", {}).get("upvotes", 0)
            if hf_id:
                hf_map[hf_id] = upvotes
        for paper in papers:
            paper["hf_upvotes"] = hf_map.get(paper["arxiv_id"], 0)
    except Exception:
        pass
    return papers


def compute_score(paper: dict[str, Any], weights: dict[str, float] | None = None) -> float:
    w = weights or DEFAULT_WEIGHTS

    # Venue bonus: 1.0 if top venue, 0.0 otherwise
    venue = (paper.get("venue") or "").lower().strip()
    venue_bonus = 1.0 if any(v in venue for v in TOP_VENUES) else 0.0

    # Citation score: log-scaled, max around 1.0 for 1000+ citations
    citations = paper.get("citation_count", 0) or 0
    # Normalize by age: citations per month
    try:
        pub_date = datetime.date.fromisoformat(paper.get("published_date", "2020-01-01"))
    except ValueError:
        pub_date = datetime.date(2020, 1, 1)
    age_months = max(1, (datetime.date.today() - pub_date).days / 30.0)
    cit_per_month = citations / age_months
    citation_score = min(1.0, math.log1p(cit_per_month) / math.log1p(50))

    # Recency score: 1.0 for today, decays over 180 days
    age_days = (datetime.date.today() - pub_date).days
    recency_score = max(0.0, 1.0 - age_days / 180.0)

    # HF upvotes: log-scaled
    hf = paper.get("hf_upvotes", 0) or 0
    hf_score = min(1.0, math.log1p(hf) / math.log1p(100))

    # GitHub stars: log-scaled
    stars = paper.get("github_stars", 0) or 0
    stars_score = min(1.0, math.log1p(stars) / math.log1p(500))

    # Social buzz: log-scaled
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
        import re
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
```

**Step 4: Run tests**

```bash
python -m pytest tests/skills/paper-daily/test_search_papers.py -v
```

Expected: All tests PASS.

**Step 5: Smoke test CLI**

```bash
cd /Users/shingz/Documents/Project/AgentBot
python bots/bot-chat/workspace/skills/paper-daily/scripts/search_papers.py --topic "Agentic RL" --top 3 --skip-enrich
```

Expected: JSON array of papers.

**Step 6: Commit**

```bash
git add bots/bot-chat/workspace/skills/paper-daily/scripts/search_papers.py tests/skills/paper-daily/
git commit -m "feat(paper-daily): add search_papers.py with dual-channel scoring"
```

---

### Task 3: Write fetch_figures.py — Figure & Table Extraction

**Files:**
- Create: `bots/bot-chat/workspace/skills/paper-daily/scripts/fetch_figures.py`
- Create: `tests/skills/paper-daily/test_fetch_figures.py`

**Step 1: Write the test**

Write to `tests/skills/paper-daily/test_fetch_figures.py`:

```python
"""Tests for figure and table extraction."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "bots/bot-chat/workspace/skills/paper-daily/scripts/fetch_figures.py"
sys.path.insert(0, str(SCRIPT.parent))


class TestAr5ivExtraction:
    """Test ar5iv HTML figure extraction."""

    def test_extract_figures_from_html(self):
        from fetch_figures import extract_figures_from_html
        html = """
        <figure id="fig1">
            <img src="https://ar5iv.labs.arxiv.org/html/2403.12345/fig1.png" />
            <figcaption>Figure 1: Architecture overview</figcaption>
        </figure>
        <figure id="tab1">
            <table><tr><th>Method</th><th>Score</th></tr><tr><td>Ours</td><td>95.2</td></tr></table>
            <figcaption>Table 1: Results</figcaption>
        </figure>
        """
        figures = extract_figures_from_html(html)
        assert len(figures) == 2
        assert figures[0]["type"] == "figure"
        assert "Architecture" in figures[0]["caption"]
        assert figures[1]["type"] == "table"

    def test_empty_html(self):
        from fetch_figures import extract_figures_from_html
        assert extract_figures_from_html("<div>no figures</div>") == []


class TestCLI:
    def test_help(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "--arxiv-id" in result.stdout
```

**Step 2: Run test — expect FAIL**

```bash
python -m pytest tests/skills/paper-daily/test_fetch_figures.py -v
```

**Step 3: Implement fetch_figures.py**

Write to `bots/bot-chat/workspace/skills/paper-daily/scripts/fetch_figures.py`:

```python
#!/usr/bin/env python3
"""Extract figures and tables from arXiv papers (ar5iv HTML or PDF fallback)."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    import fitz  # pymupdf
except ImportError:
    fitz = None

AR5IV_BASE = "https://ar5iv.labs.arxiv.org/abs/"
ARXIV_PDF_BASE = "https://arxiv.org/pdf/"


def extract_figures_from_html(html: str, base_url: str = "") -> list[dict[str, Any]]:
    """Extract figures and tables from ar5iv HTML."""
    if not BeautifulSoup:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    idx = 0

    for fig in soup.find_all("figure"):
        caption_el = fig.find("figcaption")
        caption = caption_el.get_text(" ", strip=True) if caption_el else ""

        # Check if it's a table figure
        table = fig.find("table")
        img = fig.find("img")

        if table:
            # Clean up table HTML
            table_html = str(table)
            results.append({
                "index": idx,
                "type": "table",
                "caption": caption,
                "content_html": table_html,
                "src": "",
                "path": "",
            })
            idx += 1
        elif img:
            src = img.get("src", "")
            if src and base_url:
                src = urljoin(base_url, src)
            results.append({
                "index": idx,
                "type": "figure",
                "caption": caption,
                "content_html": "",
                "src": src,
                "path": "",
            })
            idx += 1

    return results


def fetch_ar5iv(arxiv_id: str) -> str | None:
    """Fetch ar5iv HTML for a paper. Returns None on failure."""
    url = f"{AR5IV_BASE}{arxiv_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "paper-daily/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def download_image(url: str, output_path: Path) -> bool:
    """Download an image to local path."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "paper-daily/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(resp.read())
        return True
    except Exception:
        return False


def extract_from_pdf(pdf_path: Path, output_dir: Path, min_size: int = 200) -> list[dict[str, Any]]:
    """Extract images from PDF using pymupdf. Fallback when ar5iv unavailable."""
    if not fitz:
        return []
    results: list[dict[str, Any]] = []
    doc = fitz.open(str(pdf_path))
    idx = 0
    for page_num in range(len(doc)):
        page = doc[page_num]
        images = page.get_images(full=True)
        for img_info in images:
            xref = img_info[0]
            base_image = doc.extract_image(xref)
            if not base_image:
                continue
            w = base_image.get("width", 0)
            h = base_image.get("height", 0)
            if w < min_size or h < min_size:
                continue
            ext = base_image.get("ext", "png")
            img_path = output_dir / f"fig_p{page_num}_{idx}.{ext}"
            img_path.parent.mkdir(parents=True, exist_ok=True)
            img_path.write_bytes(base_image["image"])
            results.append({
                "index": idx,
                "type": "figure",
                "caption": f"Figure from page {page_num + 1}",
                "content_html": "",
                "src": "",
                "path": str(img_path),
            })
            idx += 1
    doc.close()
    return results


def download_pdf(arxiv_id: str, output_dir: Path) -> Path | None:
    """Download PDF from arXiv."""
    url = f"{ARXIV_PDF_BASE}{arxiv_id}.pdf"
    pdf_path = output_dir / f"{arxiv_id.replace('/', '_')}.pdf"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "paper-daily/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(resp.read())
        return pdf_path
    except Exception:
        return None


def fetch_figures(arxiv_id: str, output_dir: Path) -> list[dict[str, Any]]:
    """Main entry: try ar5iv first, fallback to PDF extraction."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Try ar5iv HTML
    html = fetch_ar5iv(arxiv_id)
    if html:
        base_url = f"{AR5IV_BASE}{arxiv_id}"
        figures = extract_figures_from_html(html, base_url=base_url)
        # Download figure images
        for fig in figures:
            if fig["type"] == "figure" and fig["src"]:
                ext = fig["src"].rsplit(".", 1)[-1][:4] if "." in fig["src"] else "png"
                local_path = output_dir / f"fig_{fig['index']}.{ext}"
                if download_image(fig["src"], local_path):
                    fig["path"] = str(local_path)
        if figures:
            return figures

    # Fallback: PDF
    pdf_path = download_pdf(arxiv_id, output_dir)
    if pdf_path:
        return extract_from_pdf(pdf_path, output_dir)

    return []


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract figures and tables from arXiv papers")
    parser.add_argument("--arxiv-id", required=True, help="arXiv paper ID (e.g. 2403.12345)")
    parser.add_argument("--output", required=True, help="Output directory for downloaded figures")
    args = parser.parse_args()

    figures = fetch_figures(args.arxiv_id, Path(args.output))

    # Save manifest
    manifest_path = Path(args.output) / "figures.json"
    manifest_path.write_text(json.dumps(figures, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(figures, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

**Step 4: Run tests**

```bash
python -m pytest tests/skills/paper-daily/test_fetch_figures.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add bots/bot-chat/workspace/skills/paper-daily/scripts/fetch_figures.py tests/skills/paper-daily/test_fetch_figures.py
git commit -m "feat(paper-daily): add fetch_figures.py for ar5iv/PDF extraction"
```

---

### Task 4: Write render_latex.py — Formula Rendering

**Files:**
- Create: `bots/bot-chat/workspace/skills/paper-daily/scripts/render_latex.py`
- Create: `tests/skills/paper-daily/test_render_latex.py`

**Step 1: Write the test**

Write to `tests/skills/paper-daily/test_render_latex.py`:

```python
"""Tests for LaTeX rendering."""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "bots/bot-chat/workspace/skills/paper-daily/scripts/render_latex.py"
sys.path.insert(0, str(SCRIPT.parent))


class TestRenderLatex:
    def test_render_simple_formula(self, tmp_path):
        from render_latex import render_latex
        out = tmp_path / "formula.png"
        result = render_latex(r"E = mc^2", str(out))
        assert result is True
        assert out.exists()
        assert out.stat().st_size > 100  # Not an empty file

    def test_render_complex_formula(self, tmp_path):
        from render_latex import render_latex
        out = tmp_path / "complex.png"
        result = render_latex(r"Q(s,a) = r + \gamma \max_{a'} Q(s', a')", str(out))
        assert result is True
        assert out.exists()

    def test_render_invalid_latex_still_produces_output(self, tmp_path):
        from render_latex import render_latex
        out = tmp_path / "invalid.png"
        # Should not crash, may produce partial output
        render_latex(r"\invalid_command", str(out))


class TestCLI:
    def test_help(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "--latex" in result.stdout
```

**Step 2: Run test — expect FAIL**

```bash
python -m pytest tests/skills/paper-daily/test_render_latex.py -v
```

**Step 3: Implement render_latex.py**

Write to `bots/bot-chat/workspace/skills/paper-daily/scripts/render_latex.py`:

```python
#!/usr/bin/env python3
"""Render LaTeX formulas to PNG images using matplotlib."""

from __future__ import annotations

import argparse
from pathlib import Path


def render_latex(latex: str, output_path: str, dpi: int = 150, fontsize: int = 18) -> bool:
    """Render a LaTeX string to a PNG image.

    Returns True on success, False on failure.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(0.1, 0.1))
        ax.axis("off")

        # Render the formula
        text = ax.text(
            0.5, 0.5,
            f"${latex}$",
            fontsize=fontsize,
            ha="center", va="center",
            transform=ax.transAxes,
        )

        # Adjust figure size to fit text
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        bbox = text.get_window_extent(renderer=renderer)
        # Convert from display to inches
        bbox_inches = bbox.transformed(fig.dpi_scale_trans.inverted())
        # Add padding
        pad = 0.15
        fig.set_size_inches(
            bbox_inches.width + 2 * pad,
            bbox_inches.height + 2 * pad,
        )

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(
            output_path,
            dpi=dpi,
            bbox_inches="tight",
            pad_inches=0.1,
            transparent=True,
        )
        plt.close(fig)
        return True
    except Exception as e:
        print(f"LaTeX render error: {e}", file=__import__("sys").stderr)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Render LaTeX formula to PNG")
    parser.add_argument("--latex", required=True, help="LaTeX string to render")
    parser.add_argument("--output", required=True, help="Output PNG path")
    parser.add_argument("--dpi", type=int, default=150, help="Output DPI")
    parser.add_argument("--fontsize", type=int, default=18, help="Font size")
    args = parser.parse_args()

    ok = render_latex(args.latex, args.output, dpi=args.dpi, fontsize=args.fontsize)
    if ok:
        print(f"Rendered to {args.output}")
    else:
        print("Render failed", file=__import__("sys").stderr)
        __import__("sys").exit(1)


if __name__ == "__main__":
    main()
```

**Step 4: Run tests**

```bash
python -m pytest tests/skills/paper-daily/test_render_latex.py -v
```

Expected: PASS.

**Step 5: Commit**

```bash
git add bots/bot-chat/workspace/skills/paper-daily/scripts/render_latex.py tests/skills/paper-daily/test_render_latex.py
git commit -m "feat(paper-daily): add render_latex.py for formula PNG generation"
```

---

### Task 5: Write Topic Config and Interpretation Guide

**Files:**
- Create: `bots/bot-chat/workspace/skills/paper-daily/references/topics.md`
- Create: `bots/bot-chat/workspace/skills/paper-daily/references/interpretation-guide.md`
- Create: `bots/bot-chat/workspace/skills/paper-daily/assets/templates/paper-review.md`

**Step 1: Write topics.md**

Write to `bots/bot-chat/workspace/skills/paper-daily/references/topics.md`:

```markdown
## Focus Topic (07:00 push)
Agentic RL

## Topic Pool (20:00 random pick)
- RL Training / RL Infrastructure
- RL + Code Generation / SWE Agent
- Multi-Agent RL
- RLHF / RLAIF / Reward Modeling
- Offline RL / Model-based RL
- RL for LLM Reasoning

## Scoring Weights
w1_venue: 0.25
w2_citation: 0.15
w3_recency: 0.25
w4_hf_upvotes: 0.15
w5_github_stars: 0.10
w6_social_buzz: 0.10
```

**Step 2: Write interpretation-guide.md**

Write to `bots/bot-chat/workspace/skills/paper-daily/references/interpretation-guide.md`:

```markdown
# Paper Interpretation Writing Guide

## Target Audience

RL practitioners who may lack specific sub-domain background. They understand basic RL concepts (MDP, policy gradient, value functions) but may not know the specific sub-area deeply.

## Tone

- Accessible but deep — "plain language with substance"
- Conversational but not dumbed down
- Show genuine enthusiasm when warranted, skepticism when appropriate
- No AI-speak ("pivotal role", "landscape", "delve into")
- No excessive emoji or formatting tricks

## Structure

### Title
Use the original paper title. Add a Chinese subtitle if helpful.

### One-sentence Summary
Capture the core contribution in one sentence a non-expert can understand.

### Background Section
- Start with the real-world problem or motivation
- Explain what approaches existed before and their limitations
- Make the reader feel the gap this paper fills
- 2-3 paragraphs, no jargon without explanation

### Method Section
- Lead with the core idea in one paragraph of plain language
- Then dive into technical details with figures
- For every key formula:
  1. Show the rendered formula image
  2. Immediately follow with "In plain language: ..." paragraph
  3. Explain what each symbol means
- Use paper's architecture/method figures with annotations
- If the method has multiple stages, walk through them sequentially

### Experiments Section
- Rebuild key results as Markdown tables (do not screenshot tables)
- Pick 1-2 most compelling figures from the paper
- Highlight: what beats baselines, by how much, on what tasks
- Note any surprising results or ablation insights

### My Take Section
This is the most important section — it differentiates this from a mere summary.
- What does this mean for the RL community?
- What are the real limitations (not just "future work" from the paper)?
- How does this connect to other recent work?
- What would you try next if you were building on this?
- Be honest: if the paper has weaknesses, say so

### Footer
Include paper link, code link (if available), and publication credit line.

## Image Handling

- Figures: extracted from paper via fetch_figures.py, uploaded to WeChat
- Formulas: rendered via render_latex.py, uploaded to WeChat
- Tables: write as Markdown tables, rendered as HTML by the publisher
- Cover: auto-generated with paper title

## Length

Target 2000-3000 Chinese characters (roughly 6-10 minute read).
```

**Step 3: Write paper-review.md template**

Write to `bots/bot-chat/workspace/skills/paper-daily/assets/templates/paper-review.md`:

```markdown
# {title}

> {one_sentence_summary}

## 背景：为什么要做这个？

{background_paragraph_1}

{background_paragraph_2}

## 方法：怎么做的？

{method_overview_paragraph}

{figure_1_image}

*{figure_1_caption}*

{method_detail_paragraph}

{formula_image}

**翻译成人话：** {formula_explanation}

## 实验：效果怎么样？

{experiment_overview}

| 方法 | 指标1 | 指标2 | 指标3 |
|------|-------|-------|-------|
| 基线1 | - | - | - |
| 基线2 | - | - | - |
| **本文方法** | **-** | **-** | **-** |

{experiment_analysis}

{figure_2_image}

*{figure_2_caption}*

## 我的思考

{take_paragraph_1}

{take_paragraph_2}

---

论文链接：{paper_url} | 代码：{code_url} | 每日论文推荐
```

**Step 4: Commit**

```bash
git add bots/bot-chat/workspace/skills/paper-daily/references/ bots/bot-chat/workspace/skills/paper-daily/assets/
git commit -m "feat(paper-daily): add topic config, interpretation guide, and review template"
```

---

### Task 6: Write SKILL.md — Main Skill Instructions

**Files:**
- Modify: `bots/bot-chat/workspace/skills/paper-daily/SKILL.md`

**Step 1: Write SKILL.md**

Write the complete SKILL.md:

```markdown
---
name: paper-daily
description: "Daily RL paper recommendation and interpretation for WeChat MP. Searches arXiv/Semantic Scholar/HuggingFace, scores papers via dual-channel (top-venue + fresh-frontier), generates deep interpretations with figures/formulas/tables, and publishes to WeChat. Use when: (1) cron triggers morning/evening paper push, (2) user asks for paper recommendation, (3) user says '推荐论文', '论文日推', 'paper daily', 'recommend a paper'."
---

# Paper Daily

Daily RL paper search, interpretation, and WeChat MP publishing.

## Workflow

### 1. Determine Topic

Read `references/topics.md`:
- Morning (07:00): use the Focus Topic
- Evening (20:00): randomly pick one from Topic Pool

### 2. Search Papers

```bash
python scripts/search_papers.py \
  --topic "TOPIC" \
  --exclude data/recommended.jsonl \
  --topics-file references/topics.md \
  --top 1
```

Output: JSON array with top paper metadata.

### 3. Read the Paper

Fetch full text for interpretation:
- Primary: `curl -s "https://ar5iv.labs.arxiv.org/abs/{arxiv_id}"` then extract article text
- Fallback: download PDF and read via tools

### 4. Extract Figures and Tables

```bash
python scripts/fetch_figures.py --arxiv-id {arxiv_id} --output /tmp/paper-daily/{arxiv_id}/
```

Output: `figures.json` with local paths to downloaded figures and table HTML.

### 5. Render Key Formulas

For each key formula in the paper:

```bash
python scripts/render_latex.py --latex "FORMULA" --output /tmp/paper-daily/{arxiv_id}/formulas/f{N}.png
```

### 6. Write Interpretation

Follow `references/interpretation-guide.md` for style and structure.
Use `assets/templates/paper-review.md` as the skeleton.

Key rules:
- Every formula image is followed by a "翻译成人话" paragraph
- Tables are written as Markdown tables (not screenshots)
- Figures include captions
- "我的思考" section has genuine insight
- Target length: 2000-3000 Chinese characters

Save to `/tmp/paper-review-{date}-{slot}.md`.

### 7. Publish to WeChat

Ensure `wechat-article-publisher` skill is installed. Then:

```bash
python ~/.nanobot/workspace/skills/wechat-article-publisher/scripts/publish_wechat.py \
  /tmp/paper-review-{date}-{slot}.md \
  --template standard \
  --publish --status
```

If publish_wechat.py is not at that path, check `bots/bot-chat/workspace/skills/wechat-article-publisher/`.

### 8. Record Recommendation

Append to `data/recommended.jsonl`:

```json
{"arxiv_id":"ID","title":"TITLE","topic":"TOPIC","date":"YYYY-MM-DD","slot":"morning|evening","score":0.87}
```

Create the file if it doesn't exist.

## Manual Trigger

User can say "推荐一篇论文" or "recommend a paper" to trigger a one-off run.
In manual mode, ask which topic to use if not specified.

## Topic Management

Edit `references/topics.md` to change focus topic or topic pool.
User can say "换个重点 topic" to update the focus topic.
```

**Step 2: Commit**

```bash
git add bots/bot-chat/workspace/skills/paper-daily/SKILL.md
git commit -m "feat(paper-daily): write SKILL.md with full workflow instructions"
```

---

### Task 7: Install wechat-article-publisher from ClawHub

**Step 1: Install the skill**

```bash
npx --yes clawhub@latest install wechat-article-publisher --workdir /Users/shingz/Documents/Project/AgentBot/bots/bot-chat/workspace
```

**Step 2: Configure wechat credentials**

Edit `bots/bot-chat/workspace/skills/wechat-article-publisher/config.json` — set `wechat.app_id` and `wechat.app_secret` (user provides these).

**Step 3: Test dry-run**

```bash
echo "# Test Article\n\nThis is a test." > /tmp/test-wechat.md
python bots/bot-chat/workspace/skills/wechat-article-publisher/scripts/publish_wechat.py /tmp/test-wechat.md --dry-run
```

Expected: JSON with `success: true`, `mode: dry-run`.

**Step 4: Commit**

```bash
git add bots/bot-chat/workspace/skills/wechat-article-publisher/
git commit -m "feat(paper-daily): install wechat-article-publisher from ClawHub"
```

---

### Task 8: Create data directory and recommended.jsonl

**Files:**
- Create: `bots/bot-chat/workspace/skills/paper-daily/data/recommended.jsonl`

**Step 1: Create empty data file**

```bash
mkdir -p bots/bot-chat/workspace/skills/paper-daily/data
touch bots/bot-chat/workspace/skills/paper-daily/data/recommended.jsonl
```

**Step 2: Commit**

```bash
git add bots/bot-chat/workspace/skills/paper-daily/data/
git commit -m "feat(paper-daily): add empty recommended.jsonl for dedup tracking"
```

---

### Task 9: Set Up Cron Jobs

**Step 1: Add morning cron**

Via nanobot cron tool:

```
cron(action="add", message="Run paper-daily skill: morning push, use focus topic from references/topics.md", cron_expr="0 7 * * *", tz="Asia/Shanghai")
```

**Step 2: Add evening cron**

```
cron(action="add", message="Run paper-daily skill: evening push, randomly pick topic from pool in references/topics.md", cron_expr="0 20 * * *", tz="Asia/Shanghai")
```

**Step 3: Verify cron setup**

```
cron(action="list")
```

Expected: Two jobs listed with correct schedules.

---

### Task 10: End-to-End Smoke Test

**Step 1: Run search manually**

```bash
cd /Users/shingz/Documents/Project/AgentBot
python bots/bot-chat/workspace/skills/paper-daily/scripts/search_papers.py \
  --topic "Agentic RL" \
  --exclude bots/bot-chat/workspace/skills/paper-daily/data/recommended.jsonl \
  --topics-file bots/bot-chat/workspace/skills/paper-daily/references/topics.md \
  --top 1
```

Verify: returns valid JSON with a paper.

**Step 2: Run figure extraction on returned paper**

```bash
python bots/bot-chat/workspace/skills/paper-daily/scripts/fetch_figures.py \
  --arxiv-id {ARXIV_ID_FROM_STEP_1} \
  --output /tmp/paper-daily/{ARXIV_ID}/
```

Verify: figures.json created with entries.

**Step 3: Test LaTeX rendering**

```bash
python bots/bot-chat/workspace/skills/paper-daily/scripts/render_latex.py \
  --latex "Q(s,a) = r + \gamma \max_{a'} Q(s', a')" \
  --output /tmp/paper-daily/test_formula.png
```

Verify: PNG file created, viewable.

**Step 4: Dry-run WeChat publish**

```bash
python bots/bot-chat/workspace/skills/wechat-article-publisher/scripts/publish_wechat.py \
  /tmp/test-wechat.md --dry-run
```

Verify: `success: true`.

**Step 5: Commit final state**

```bash
git add -A
git commit -m "feat(paper-daily): complete skill with all scripts, config, and tests"
```

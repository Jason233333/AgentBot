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
        q = build_arxiv_query("RL + Code Generation / SWE Agent", categories=["cs.LG"])
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

    def test_no_venue_no_citations_still_scores(self):
        from search_papers import compute_score
        paper = {
            "venue": "",
            "citation_count": 0,
            "published_date": "2026-03-20",
            "hf_upvotes": 0,
            "github_stars": 0,
            "social_buzz": 0,
        }
        score = compute_score(paper)
        assert score >= 0


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

    def test_filter_empty_exclude(self):
        from search_papers import filter_recommended
        papers = [{"arxiv_id": "2403.11111", "title": "A"}]
        result = filter_recommended(papers, set())
        assert len(result) == 1

    def test_load_exclude_ids(self, tmp_path):
        from search_papers import load_exclude_ids
        f = tmp_path / "rec.jsonl"
        f.write_text('{"arxiv_id":"2403.11111","title":"A"}\n{"arxiv_id":"2403.22222","title":"B"}\n')
        ids = load_exclude_ids(str(f))
        assert ids == {"2403.11111", "2403.22222"}

    def test_load_exclude_ids_missing_file(self):
        from search_papers import load_exclude_ids
        ids = load_exclude_ids("/nonexistent/path.jsonl")
        assert ids == set()


class TestCLI:
    """Test CLI interface."""

    def test_help_flag(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "--topic" in result.stdout

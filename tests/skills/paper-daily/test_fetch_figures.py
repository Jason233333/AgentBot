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

    def test_figure_with_no_caption(self):
        from fetch_figures import extract_figures_from_html
        html = '<figure><img src="http://example.com/img.png" /></figure>'
        figures = extract_figures_from_html(html)
        assert len(figures) == 1
        assert figures[0]["caption"] == ""

    def test_table_html_preserved(self):
        from fetch_figures import extract_figures_from_html
        html = """
        <figure>
            <table><tr><td>A</td><td>B</td></tr></table>
            <figcaption>Table 2</figcaption>
        </figure>
        """
        figures = extract_figures_from_html(html)
        assert len(figures) == 1
        assert "<table>" in figures[0]["content_html"]

    def test_base_url_resolved(self):
        from fetch_figures import extract_figures_from_html
        html = '<figure><img src="/html/123/fig.png" /></figure>'
        figures = extract_figures_from_html(html, base_url="https://ar5iv.labs.arxiv.org/abs/123")
        assert figures[0]["src"].startswith("https://")


class TestCLI:
    def test_help(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "--arxiv-id" in result.stdout

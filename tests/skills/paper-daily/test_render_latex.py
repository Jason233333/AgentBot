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
        assert out.stat().st_size > 100

    def test_render_complex_formula(self, tmp_path):
        from render_latex import render_latex
        out = tmp_path / "complex.png"
        result = render_latex(r"Q(s,a) = r + \gamma \max_{a'} Q(s', a')", str(out))
        assert result is True
        assert out.exists()

    def test_render_creates_parent_dirs(self, tmp_path):
        from render_latex import render_latex
        out = tmp_path / "sub" / "dir" / "formula.png"
        result = render_latex(r"x^2", str(out))
        assert result is True
        assert out.exists()


class TestCLI:
    def test_help(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "--latex" in result.stdout

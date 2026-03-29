#!/usr/bin/env python3
"""Extract figures and tables from arXiv papers (ar5iv HTML or PDF fallback)."""

from __future__ import annotations

import argparse
import json
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

        table = fig.find("table")
        img = fig.find("img")

        if table:
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

    html = fetch_ar5iv(arxiv_id)
    if html:
        base_url = f"{AR5IV_BASE}{arxiv_id}"
        figures = extract_figures_from_html(html, base_url=base_url)
        for fig in figures:
            if fig["type"] == "figure" and fig["src"]:
                ext = fig["src"].rsplit(".", 1)[-1][:4] if "." in fig["src"] else "png"
                local_path = output_dir / f"fig_{fig['index']}.{ext}"
                if download_image(fig["src"], local_path):
                    fig["path"] = str(local_path)
        if figures:
            return figures

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

    manifest_path = Path(args.output) / "figures.json"
    manifest_path.write_text(json.dumps(figures, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(figures, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

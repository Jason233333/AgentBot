#!/usr/bin/env python3
"""Render LaTeX formulas to PNG images using matplotlib."""

from __future__ import annotations

import argparse
import sys
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

        text = ax.text(
            0.5, 0.5,
            f"${latex}$",
            fontsize=fontsize,
            ha="center", va="center",
            transform=ax.transAxes,
        )

        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        bbox = text.get_window_extent(renderer=renderer)
        bbox_inches = bbox.transformed(fig.dpi_scale_trans.inverted())
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
        print(f"LaTeX render error: {e}", file=sys.stderr)
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
        print("Render failed", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

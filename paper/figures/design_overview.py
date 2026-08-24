#!/usr/bin/env python3
"""Monochrome vector overview for the paper's design section."""
from __future__ import annotations

import os
import sys

from plot_style import apply_paper_style

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "design_overview.pdf")


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    apply_paper_style(matplotlib)
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, Rectangle

    fig, ax = plt.subplots(figsize=(10.6, 4.25))
    ax.set_xlim(0, 10.6)
    ax.set_ylim(0, 4.25)
    ax.axis("off")

    ink = "#111111"
    white = "#ffffff"
    pale = "#f3f3f3"
    mid = "#e3e3e3"

    def box(x: float, y: float, w: float, h: float, title: str, body: str,
            face: str = pale, edge: str = ink, title_size: float = 13,
            body_size: float = 11) -> None:
        patch = Rectangle((x, y), w, h, linewidth=1.1,
                          edgecolor=edge, facecolor=face)
        ax.add_patch(patch)
        ax.text(x+w/2, y+h*.74, title, ha="center", va="center",
                fontsize=title_size,
                fontweight="bold", color=ink)
        ax.text(x+w/2, y+h*.34, body, ha="center", va="center",
                fontsize=body_size, color=ink, linespacing=1.15)

    def arrow(x1: float, y1: float, x2: float, y2: float, color: str = ink) -> None:
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                                    mutation_scale=11, linewidth=1.0, color=color))

    stages = [(".an", "source"), ("Tokens", "lexer"), ("AST", "parser"),
              ("HIR", "typed"), ("CFG IR", "checks"), ("LLVM IR", "branches"),
              ("native", "trap")]
    sx, sy, sw, gap = .25, 3.43, 1.18, .25
    for i, (title, body) in enumerate(stages):
        x = sx + i*(sw+gap)
        box(x, sy, sw, .62, title, body, face=white,
            title_size=12.5, body_size=10.5)
        if i:
            arrow(x-gap+.02, sy+.31, x-.04, sy+.31)

    box(.35, 1.75, 2.85, 1.08, "Tiered values",
        "T*  40B  {data, lock, key, index, size}\n"
        "T[] 32B  {data, lock, key, size}\n"
        "T&  24B  {data, lock, key}")
    box(3.88, 1.75, 2.85, 1.08, "CFG safety obligations",
        "live + full-span bounds\nnonempty/reference origin\n"
        "arithmetic, compare, ptrdiff, delete", face=mid)
    box(7.28, 1.75, 2.85, 1.08, "LLVM enforcement",
        "check block dominates access\nfailed edge -> absorbing trap\n"
        "raw/nocheck/check modes", face=pale)
    arrow(3.2, 2.29, 3.84, 2.29)
    arrow(6.73, 2.29, 7.24, 2.29)

    box(.85, .18, 3.85, .84, "Heap lifetime",
        "32B stable header: lock | capacity | next | reserved\n"
        "delete: SENTINEL -> free list; reuse: fresh key", face=mid)
    box(5.9, .18, 3.85, .84, "Stack lifetime",
        "one lock slot per frame; entry re-key\n"
        "every return writes SENTINEL", face=pale)
    arrow(2.78, 1.70, 2.78, 1.07)
    arrow(8.02, 1.70, 8.02, 1.07)

    fig.tight_layout(pad=.4)
    fig.savefig(OUT, bbox_inches="tight")
    print(f"OK: {OUT} generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

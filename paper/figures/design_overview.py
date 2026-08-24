#!/usr/bin/env python3
"""Monochrome vector overview for the paper's design section."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "design_overview.pdf")


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, Rectangle

    fig, ax = plt.subplots(figsize=(10.6, 5.15))
    ax.set_xlim(0, 10.6)
    ax.set_ylim(0, 5.15)
    ax.axis("off")

    ink = "#111111"
    white = "#ffffff"
    pale = "#f3f3f3"
    mid = "#e3e3e3"

    def box(x: float, y: float, w: float, h: float, title: str, body: str,
            face: str = pale, edge: str = ink) -> None:
        patch = Rectangle((x, y), w, h, linewidth=1.1,
                          edgecolor=edge, facecolor=face)
        ax.add_patch(patch)
        ax.text(x+w/2, y+h-.22, title, ha="center", va="top", fontsize=10.5,
                fontweight="bold", color=ink)
        ax.text(x+w/2, y+h/2-.12, body, ha="center", va="center", fontsize=8.5,
                color=ink, linespacing=1.3)

    def arrow(x1: float, y1: float, x2: float, y2: float, color: str = ink) -> None:
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                                    mutation_scale=11, linewidth=1.0, color=color))

    stages = [(".an", "source"), ("Tokens", "lexer"), ("AST", "parser"),
              ("HIR", "typed"), ("CFG IR", "checks"), ("LLVM IR", "branches"),
              ("native", "trap")]
    sx, sy, sw, gap = .25, 4.18, 1.18, .25
    for i, (title, body) in enumerate(stages):
        x = sx + i*(sw+gap)
        box(x, sy, sw, .72, title, body, face=white)
        if i:
            arrow(x-gap+.02, sy+.36, x-.04, sy+.36)

    box(.35, 2.25, 2.85, 1.25, "Tiered values",
        "T*  40B  {data, lock, key, index, size}\n"
        "T[] 32B  {data, lock, key, size}\n"
        "T&  24B  {data, lock, key}")
    ax.text(1.78, 2.07, "validated degradation  T* -> T[] -> T&",
            ha="center", fontsize=8, color=ink)

    box(3.88, 2.25, 2.85, 1.25, "CFG safety obligations",
        "live + full-span bounds\nnonempty/reference origin\n"
        "arithmetic, compare, ptrdiff, delete", face=mid)
    box(7.28, 2.25, 2.85, 1.25, "LLVM enforcement",
        "check block dominates access\nfailed edge -> absorbing trap\n"
        "raw/nocheck/check modes", face=pale)
    arrow(3.2, 2.88, 3.84, 2.88)
    arrow(6.73, 2.88, 7.24, 2.88)

    box(.85, .35, 3.85, 1.05, "Heap lifetime",
        "32B stable header: lock | capacity | next | reserved\n"
        "delete: SENTINEL -> free list; reuse: fresh key", face=mid)
    box(5.9, .35, 3.85, 1.05, "Stack lifetime",
        "one lock slot per frame; entry re-key\n"
        "every return writes SENTINEL", face=pale)
    arrow(2.78, 2.22, 2.78, 1.44)
    arrow(8.02, 2.22, 8.02, 1.44)

    fig.tight_layout(pad=.4)
    fig.savefig(OUT, bbox_inches="tight")
    print(f"OK: {OUT} generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

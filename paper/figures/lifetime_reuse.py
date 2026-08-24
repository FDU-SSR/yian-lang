#!/usr/bin/env python3
"""Heap-block and stable frame-lock key lifecycle as a vector PDF."""
from __future__ import annotations

import os
import sys

from plot_style import apply_paper_style

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "lifetime_reuse.pdf")


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    apply_paper_style(matplotlib)
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, Rectangle

    fig, ax = plt.subplots(figsize=(10.8, 2.65))
    ax.set_xlim(0, 10.8)
    ax.set_ylim(0, 2.65)
    ax.axis("off")

    ink = "#111111"
    active = "#ffffff"
    inactive = "#e3e3e3"
    renewed = "#f3f3f3"

    def state(x: float, y: float, w: float, title: str, body: str,
              face: str, hatch: str = "") -> None:
        patch = Rectangle((x, y), w, .78, linewidth=1.1,
                          edgecolor=ink, facecolor=face, hatch=hatch)
        ax.add_patch(patch)
        ax.text(x + w/2, y + .59, title, ha="center", va="center",
                fontsize=11.8, fontweight="bold", color=ink)
        ax.text(x + w/2, y + .25, body, ha="center", va="center",
                fontsize=10.5, color=ink, linespacing=1.15)

    def arrow(x1: float, y: float, x2: float, label: str,
              linestyle: str = "-") -> None:
        ax.add_patch(FancyArrowPatch(
            (x1, y), (x2, y), arrowstyle="-|>", mutation_scale=12,
            linewidth=1.15, color=ink, linestyle=linestyle,
        ))
        ax.text((x1+x2)/2, y+.12, label, ha="center", va="bottom",
                fontsize=10.3, color=ink, fontweight="bold")

    ax.text(.12, 1.99, "heap block b", ha="left", va="center",
            fontsize=11.5, fontweight="bold", color=ink)
    state(1.28, 1.60, 2.35, "active allocation",
          "header.lock = k\nnew pointer carries k", active)
    arrow(3.67, 1.99, 4.30, "delete")
    state(4.34, 1.60, 2.12, "free, still mapped",
          "header.lock = SENTINEL\nold pointer still carries k", inactive)
    arrow(6.50, 1.99, 7.15, "reuse b", "--")
    state(7.19, 1.60, 3.25, "new active allocation",
          "header.lock = k'  (k' != k)\nold pointer fails live; new pointer passes",
          renewed)

    ax.text(.12, .64, "stable slot l", ha="left", va="center",
            fontsize=11.5, fontweight="bold", color=ink)
    state(1.28, .25, 2.35, "frame entry",
          "slot[l] = k\nescaped pointer carries k", active)
    arrow(3.67, .64, 4.30, "return")
    state(4.34, .25, 2.12, "inactive frame",
          "slot[l] = SENTINEL\nescaped pointer fails live", inactive)
    arrow(6.50, .64, 7.15, "slot reuse", "--")
    state(7.19, .25, 3.25, "fresh frame lifetime",
          "slot[l] = k'  (k' != k)\nold pointer remains invalid",
          renewed)

    fig.tight_layout(pad=.25)
    fig.savefig(OUT, bbox_inches="tight")
    print(f"OK: {OUT} generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Heap-block and stack-frame key/lock lifecycle as a vector PDF."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "lifetime_reuse.pdf")


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, Rectangle

    fig, ax = plt.subplots(figsize=(10.8, 3.25))
    ax.set_xlim(0, 10.8)
    ax.set_ylim(0, 3.25)
    ax.axis("off")

    ink = "#111111"
    active = "#ffffff"
    inactive = "#e3e3e3"
    renewed = "#f3f3f3"

    def state(x: float, y: float, w: float, title: str, body: str,
              face: str, hatch: str = "") -> None:
        patch = Rectangle((x, y), w, .92, linewidth=1.1,
                          edgecolor=ink, facecolor=face, hatch=hatch)
        ax.add_patch(patch)
        ax.text(x + w/2, y + .70, title, ha="center", va="center",
                fontsize=9.5, fontweight="bold", color=ink)
        ax.text(x + w/2, y + .35, body, ha="center", va="center",
                fontsize=8.3, color=ink, linespacing=1.25)

    def arrow(x1: float, y: float, x2: float, label: str,
              linestyle: str = "-") -> None:
        ax.add_patch(FancyArrowPatch(
            (x1, y), (x2, y), arrowstyle="-|>", mutation_scale=12,
            linewidth=1.15, color=ink, linestyle=linestyle,
        ))
        ax.text((x1+x2)/2, y+.12, label, ha="center", va="bottom",
                fontsize=8.3, color=ink, fontweight="bold")

    ax.text(.12, 2.42, "heap block b", ha="left", va="center",
            fontsize=10, fontweight="bold", color=ink)
    state(1.28, 1.96, 2.35, "active allocation",
          "header.lock = k\nnew pointer carries k", active)
    arrow(3.67, 2.42, 4.30, "delete")
    state(4.34, 1.96, 2.12, "free, still mapped",
          "header.lock = SENTINEL\nold pointer still carries k", inactive)
    arrow(6.50, 2.42, 7.15, "reuse b", "--")
    state(7.19, 1.96, 3.25, "new active allocation",
          "header.lock = k'  (k' != k)\nold pointer fails live; new pointer passes",
          renewed)

    ax.text(.12, .86, "frame slot l", ha="left", va="center",
            fontsize=10, fontweight="bold", color=ink)
    state(1.28, .40, 2.35, "frame entry",
          "slot[l] = f\nescaped pointer carries f", active)
    arrow(3.67, .86, 4.30, "return")
    state(4.34, .40, 2.12, "inactive frame",
          "slot[l] = SENTINEL\nescaped pointer fails live", inactive)
    arrow(6.50, .86, 7.15, "re-entry", "--")
    state(7.19, .40, 3.25, "fresh frame lifetime",
          "slot[l] = f'  (f' != f)\nold pointer remains invalid",
          renewed)

    fig.tight_layout(pad=.25)
    fig.savefig(OUT, bbox_inches="tight")
    print(f"OK: {OUT} generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""fig1_cost_decomposition.py — Three-state cost decomposition grouped bar chart

Output: paper/figures/fig1_cost_decomposition.pdf (or argv[1])

Data source: paper/data/performance.csv (frozen snapshot of docs/performance.csv)
Columns: representation cost = ②/① (nocheck / raw), check cost = ③/② (check /
nocheck), total cost = ③/① (check / raw). Total is multiplicative:
③/① = ②/① × ③/②. The grouped layout keeps all three reported ratios explicit.

Methodology: three-state adjacent-session protocol (assessment.md §1.4), per-benchmark
ratios; authoritative numbers from data/performance.csv.

Run: python3 paper/figures/fig1_cost_decomposition.py
Dependency: matplotlib (optional; if missing, prints a data summary and exits)
"""
from __future__ import annotations

import csv
import io
import math
import os
import sys

from plot_style import apply_paper_style

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "..", "data", "performance.csv")
OUT_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "fig1_cost_decomposition.pdf")


def load_rows(path: str) -> list[dict[str, str]]:
    """Load CSV rows, skipping '#'-prefixed comment lines (they break DictReader)."""
    with open(path, encoding="utf-8") as f:
        clean = "".join(line for line in f if not line.lstrip().startswith("#"))
    return list(csv.DictReader(io.StringIO(clean)))


def main() -> int:
    rows = load_rows(CSV_PATH)
    assert len(rows) == 14, f"期望 14 基准, 实际 {len(rows)}"

    names = [r["基准"] for r in rows]
    rep = [float(r["表示成本倍率(②/①)"]) for r in rows]
    chk = [float(r["检查成本倍率(③/②)"]) for r in rows]
    total = [float(r["总成本倍率(③/①)"]) for r in rows]
    try:
        import matplotlib

        matplotlib.use("Agg")
        apply_paper_style(matplotlib)
        import matplotlib.pyplot as plt
    except ImportError:  # matplotlib optional
        print("matplotlib not installed; printing data summary (figure not generated)")
        print("install: pip install matplotlib")
        print(f"{'bench':<14}{'rep 02/01':>10}{'chk 03/02':>10}{'total 03/01':>11}")
        for n, a, b, c in zip(names, rep, chk, total):
            print(f"{n:<14}{a:>10.2f}{b:>10.2f}{c:>11.2f}")
        return 1

    # Match the final two-column width so LaTeX does not shrink typography.
    fig, ax = plt.subplots(figsize=(7.0, 3.55))

    x = list(range(len(names)))

    colors_rep = "#ffffff"
    colors_chk = "#bdbdbd"
    colors_total = "#4d4d4d"

    width = .24
    ax.bar([i-width for i in x], rep, width=width, color=colors_rep,
           edgecolor="black", linewidth=0.7,
           label="nocheck/raw: representation and lifetime")
    ax.bar(x, chk, width=width, color=colors_chk,
           edgecolor="black", linewidth=0.7, hatch="///",
           label="check/nocheck: emitted checks")
    ax.bar([i+width for i in x], total, width=width, color=colors_total,
           edgecolor="black", linewidth=0.7, hatch="xx",
           label="check/raw: end-to-end")
    for i in range(len(names)):
        ax.annotate(f"{total[i]:.2f}x", xy=(i + width, total[i]),
                    xytext=(0, 4), textcoords="offset points",
                    ha="center", va="bottom", fontsize=7.5,
                    color="black", fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=42, ha="right", fontsize=7.5)
    ax.set_ylabel("median runtime ratio (log scale)", fontsize=8.5)

    ax.set_yscale("log")
    # Keep the tallest value label clear of the legend and top frame.
    ax.set_ylim(.55, 5.2)
    ticks = [.6, .75, 1.0, 1.5, 2.0, 3.0, 4.0]
    ax.set_yticks(ticks)
    ax.set_yticklabels([f"{t:g}x" for t in ticks], fontsize=8)
    ax.axhline(1, color="black", linewidth=0.8)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.01),
              fontsize=7.6, ncol=3, frameon=False,
              handlelength=1.5, columnspacing=1.1, handletextpad=.45)

    fig.tight_layout(pad=.7)
    fig.savefig(OUT_PATH, bbox_inches="tight")
    print(f"OK: {OUT_PATH} generated ({len(names)} benchmarks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""fig1_cost_decomposition.py — Three-state cost decomposition grouped bar chart

Output: paper/figures/fig1_cost_decomposition.pdf (or argv[1])

Data source: paper/data/performance.csv (frozen snapshot of docs/performance.csv)
Columns: representation cost = ②/① (nocheck / raw), check cost = ③/② (check /
nocheck), total cost = ③/① (check / raw). Total is multiplicative:
③/① = ②/① × ③/②. The grouped layout keeps all three reported ratios explicit.

Methodology: three-state adjacent-session protocol (assessment.md §1.4), per-benchmark
ratios; authoritative numbers from data/performance.csv. The 1.2x threshold follows
assessment.md (the current snapshot has total cost <= 1.2x in 8/14 benchmarks).

Run: python3 paper/figures/fig1_cost_decomposition.py
Dependency: matplotlib (optional; if missing, prints a data summary and exits)
"""
from __future__ import annotations

import csv
import io
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "..", "data", "performance.csv")
OUT_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "fig1_cost_decomposition.pdf")
THRESHOLD = 1.2  # total-cost ratio threshold


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
    n_ok = sum(1 for t in total if t <= THRESHOLD)

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:  # matplotlib optional
        print("matplotlib not installed; printing data summary (figure not generated)")
        print("install: pip install matplotlib")
        print(f"{'bench':<14}{'rep 02/01':>10}{'chk 03/02':>10}{'total 03/01':>11}")
        for n, a, b, c in zip(names, rep, chk, total):
            print(f"{n:<14}{a:>10.2f}{b:>10.2f}{c:>11.2f}")
        return 1

    fig, ax = plt.subplots(figsize=(12, 6))

    x = list(range(len(names)))

    colors_rep = "#ffffff"
    colors_chk = "#bdbdbd"
    colors_total = "#4d4d4d"

    width = .24
    ax.bar([i-width for i in x], rep, width=width, color=colors_rep,
           edgecolor="black", linewidth=0.7,
           label="R: nocheck/raw (representation)")
    ax.bar(x, chk, width=width, color=colors_chk,
           edgecolor="black", linewidth=0.7, hatch="///",
           label="C: check/nocheck (checks)")
    ax.bar([i+width for i in x], total, width=width, color=colors_total,
           edgecolor="black", linewidth=0.7, hatch="xx",
           label="E: check/raw (end-to-end)")
    for i in range(len(names)):
        ax.text(i+width, total[i] * 1.04, f"{total[i]:.2f}x",
                ha="center", va="bottom", fontsize=8,
                color="black", fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.set_ylabel("median runtime ratio (log scale)")

    # threshold line: 1.2x
    ax.axhline(THRESHOLD, color="black", linestyle="--", linewidth=1)
    ax.text(len(names) - 0.5, THRESHOLD, f"  {THRESHOLD}x",
            ha="right", va="bottom", color="black", fontsize=8)

    ax.set_yscale("log")
    ax.set_ylim(.55, 4.7)
    ticks = [.6, .75, 1.0, 1.2, 1.5, 2.0, 3.0, 4.0]
    ax.set_yticks(ticks)
    ax.set_yticklabels([f"{t:g}x" for t in ticks])
    ax.axhline(1, color="black", linewidth=0.8)
    ax.legend(loc="upper left", fontsize=8, ncol=3, frameon=False)

    fig.tight_layout()
    fig.savefig(OUT_PATH, bbox_inches="tight")
    print(f"OK: {OUT_PATH} generated ({len(names)} benchmarks, "
          f"total cost <= {THRESHOLD}x: {n_ok}/14)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

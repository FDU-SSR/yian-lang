#!/usr/bin/env python3
"""fig1_cost_decomposition.py — Three-state cost decomposition stacked bar chart

Output: paper/figures/fig1_cost_decomposition.png

Data source: paper/data/performance.csv (frozen snapshot of docs/performance.csv)
Columns: representation cost = ②/① (nocheck / raw), check cost = ③/② (check /
nocheck), total cost = ③/① (check / raw). Total is multiplicative:
③/① = ②/① × ③/②, so we stack on a log2 axis (log2(total) = log2(rep) + log2(check)).

Methodology: three-state adjacent-session protocol (assessment.md §1.4), per-benchmark
ratios; authoritative numbers from data/performance.csv. The 1.2x threshold follows
assessment.md §6 (total cost <= 1.2x in exactly 7/14 benchmarks).

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
OUT_PATH = os.path.join(HERE, "fig1_cost_decomposition.png")
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
        print("matplotlib not installed; printing data summary (PNG not generated)")
        print("install: pip install matplotlib")
        print(f"{'bench':<14}{'rep 02/01':>10}{'chk 03/02':>10}{'total 03/01':>11}")
        for n, a, b, c in zip(names, rep, chk, total):
            print(f"{n:<14}{a:>10.2f}{b:>10.2f}{c:>11.2f}")
        return 1

    fig, ax = plt.subplots(figsize=(12, 6))

    # log2 stacked: bottom segment = log2(rep), top segment = log2(check)
    rep_h = [math.log2(x) for x in rep]
    chk_h = [math.log2(x) for x in chk]
    x = list(range(len(names)))

    colors_rep = "#9ecae1"
    colors_chk = "#3182bd"
    ok_color = "#31a354"
    bad_color = "#de2d26"

    for i in range(len(names)):
        ok = total[i] <= THRESHOLD
        bar_color = ok_color if ok else bad_color
        ax.bar(i, rep_h[i], bottom=0, width=0.6, color=colors_rep,
               edgecolor="black", linewidth=0.5)
        ax.bar(i, chk_h[i], bottom=rep_h[i], width=0.6, color=colors_chk,
               edgecolor="black", linewidth=0.5)
        # annotate total ratio
        ax.text(i, math.log2(total[i]) + 0.05, f"{total[i]:.2f}x",
                ha="center", va="bottom", fontsize=8,
                color=bar_color, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.set_ylabel("ratio vs 01raw (log2 scale, multiplicative)")
    ax.set_title("Three-state cost decomposition: representation (02/01) + check "
                 "(03/02) = total (03/01)\n"
                 f"14 benchmarks, total cost <= {THRESHOLD}x in {n_ok}/14 (green), "
                 f"rest red",
                 fontsize=11)

    # threshold line: 1.2x
    ax.axhline(math.log2(THRESHOLD), color="gray", linestyle="--", linewidth=1)
    ax.text(len(names) - 0.5, math.log2(THRESHOLD), f"  {THRESHOLD}x",
            ha="right", va="bottom", color="gray", fontsize=8)

    # y ticks back to ratios (2^y)
    ticks = [0.5, 0.75, 1.0, 1.2, 1.5, 2.0, 3.0, 4.0]
    ax.set_yticks([math.log2(t) for t in ticks])
    ax.set_yticklabels([f"{t:g}x" for t in ticks])
    ax.axhline(0, color="black", linewidth=0.8)

    ax.legend(handles=[
        plt.Rectangle((0, 0), 1, 1, color=colors_rep, label="representation 02/01"),
        plt.Rectangle((0, 0), 1, 1, color=colors_chk, label="check 03/02"),
        plt.Rectangle((0, 0), 1, 1, color=ok_color, label=f"total <= {THRESHOLD}x"),
        plt.Rectangle((0, 0), 1, 1, color=bad_color, label=f"total > {THRESHOLD}x"),
    ], loc="upper left", fontsize=8, ncol=2)

    fig.text(0.01, 0.01,
             "Data: paper/data/performance.csv (frozen snapshot of docs/performance.csv, "
             "2026-08-24)\n"
             "Method: three-state adjacent-session protocol (assessment.md sec 1.4); "
             "log2 stacking = multiplicative decomposition",
             fontsize=7, color="gray")

    fig.tight_layout(rect=[0, 0.05, 1, 1])
    fig.savefig(OUT_PATH, dpi=200)
    print(f"OK: {OUT_PATH} generated ({len(names)} benchmarks, "
          f"total cost <= {THRESHOLD}x: {n_ok}/14)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

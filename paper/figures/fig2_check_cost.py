#!/usr/bin/env python3
"""fig2_check_cost.py — Check cost vs benchmark (column 03/02)

Output: paper/figures/fig2_check_cost.png

Data source: paper/data/performance.csv (frozen snapshot of docs/performance.csv)
Shows the check cost ratio per benchmark (03 check / 02 nocheck), annotating
10/14 <= 1.2x. Check cost and total cost are independent facts: check cost
<= 1.2x in 10/14, total cost <= 1.2x in 7/14 (assessment.md sec 6).

Run: python3 paper/figures/fig2_check_cost.py
Dependency: matplotlib (optional; if missing, prints a data summary and exits)
"""
from __future__ import annotations

import csv
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "..", "data", "performance.csv")
OUT_PATH = os.path.join(HERE, "fig2_check_cost.png")
THRESHOLD = 1.2


def load_rows(path: str) -> list[dict[str, str]]:
    """Load CSV rows, skipping '#'-prefixed comment lines (they break DictReader)."""
    with open(path, encoding="utf-8") as f:
        clean = "".join(line for line in f if not line.lstrip().startswith("#"))
    return list(csv.DictReader(io.StringIO(clean)))


def main() -> int:
    rows = load_rows(CSV_PATH)
    assert len(rows) == 14, f"期望 14 基准, 实际 {len(rows)}"

    names = [r["基准"] for r in rows]
    chk = [float(r["检查成本倍率(③/②)"]) for r in rows]
    n_ok = sum(1 for c in chk if c <= THRESHOLD)

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:  # matplotlib optional
        print("matplotlib not installed; printing data summary (PNG not generated)")
        print("install: pip install matplotlib")
        print(f"{'bench':<14}{'check 03/02':>12}")
        for n, c in zip(names, chk):
            mark = "<=1.2x" if c <= THRESHOLD else ">1.2x"
            print(f"{n:<14}{c:>10.2f}  {mark}")
        return 1

    x = list(range(len(names)))
    ok_color = "#31a354"
    bad_color = "#de2d26"
    colors = [ok_color if c <= THRESHOLD else bad_color for c in chk]

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.bar(x, chk, width=0.6, color=colors, edgecolor="black", linewidth=0.5)

    for i, c in enumerate(chk):
        ax.text(i, c + 0.02, f"{c:.2f}x", ha="center", va="bottom", fontsize=8)

    ax.axhline(THRESHOLD, color="gray", linestyle="--", linewidth=1)
    ax.text(len(names) - 0.5, THRESHOLD, f"  {THRESHOLD}x",
            ha="right", va="bottom", color="gray", fontsize=9)

    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.set_ylabel("check cost ratio (03 check / 02 nocheck)")
    ax.set_title(f"Check cost vs benchmark (column 03/02): {n_ok}/14 <= {THRESHOLD}x",
                 fontsize=11)
    ax.axhline(1.0, color="black", linewidth=0.8)
    ax.set_ylim(0, max(chk) * 1.15)

    ax.legend(handles=[
        plt.Rectangle((0, 0), 1, 1, color=ok_color, label=f"check cost <= {THRESHOLD}x"),
        plt.Rectangle((0, 0), 1, 1, color=bad_color, label=f"check cost > {THRESHOLD}x"),
    ], loc="upper left", fontsize=8)

    fig.text(0.01, 0.01,
             "Data: paper/data/performance.csv (frozen snapshot of docs/performance.csv, "
             "2026-08-23)\n"
             "Note: check cost (03/02) and total cost (03/01) are independent facts; "
             "check <= 1.2x in 10/14,\n"
             "      total <= 1.2x in 7/14 (assessment.md sec 6); do not conflate",
             fontsize=7, color="gray")

    fig.tight_layout(rect=[0, 0.06, 1, 1])
    fig.savefig(OUT_PATH, dpi=200)
    print(f"OK: {OUT_PATH} generated ({len(names)} benchmarks, "
          f"check cost <= {THRESHOLD}x: {n_ok}/14)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Mechanism-level summary for the 38-CVE paired evaluation.

The dense 38x12 matrix remains an appendix candidate; this figure answers the
main-text question directly by showing the primary interception mechanism.
"""
from __future__ import annotations

from collections import Counter
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SOURCE = os.path.join(ROOT, "tests", "fat_cve", "docs", "MECHANISMS.md")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "fig3_cve_summary.pdf")


def parse() -> list[int]:
    values: list[int] = []
    in_table = False
    with open(SOURCE, encoding="utf-8") as handle:
        for line in handle:
            if "完整归属明细" in line:
                in_table = True
                continue
            if not in_table or not line.startswith("| CVE-"):
                continue
            cols = [cell.strip() for cell in line.split("|")]
            if not re.fullmatch(r"CVE-\d{4}-\d{4,5}", cols[1]):
                continue
            match = re.search(r"(\d+)", cols[3])
            if match:
                values.append(int(match.group(1)))
    return values


def main() -> int:
    counts = Counter(parse())
    expected = {4: 25, 5: 10, 6: 1, 8: 2}
    assert sum(counts.values()) == 38, counts
    assert {key: counts[key] for key in expected} == expected, counts

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = ["Spatial bounds\n(in_bounds)", "Temporal liveness\n(live)",
              "Heap-only delete\n(is_heap)", "Frame re-key"]
    values = [25, 10, 1, 2]
    hatches = ["", "///", "xx", "..."]
    fig, ax = plt.subplots(figsize=(8.3, 3.9))
    bars = ax.bar(labels, values, color="#f2f2f2", edgecolor="black", linewidth=.8)
    for bar, hatch in zip(bars, hatches):
        bar.set_hatch(hatch)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width()/2, value + .5, str(value),
                ha="center", va="bottom", fontsize=11, fontweight="bold")
    ax.set_ylim(0, 29)
    ax.set_ylabel("CVE pairs (vulnerable + fixed)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#dddddd", linewidth=.6, zorder=0)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(OUT, bbox_inches="tight")
    print(f"OK: {OUT} generated; distribution {values}; 38 CVEs / 76 cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

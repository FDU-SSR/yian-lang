#!/usr/bin/env python3
"""fig4_asan_compare.py — ASan comparison (sec 10.7 historical benchmarks)

Output: paper/figures/fig4_asan_compare.png

Data source: docs/security-code.md sec 10.7 (L322, measured 2026-08-14, archived
in build/bench/results.md)
NOTE: the 3 loads (ptr_traverse/alloc_dense/mixed) were removed from the tree in
2026-08. This figure is a HISTORICAL benchmark comparison, not current shootout
suite data. Must be flagged as such in the paper (assessment.md sec 5.1).

Run: python3 paper/figures/fig4_asan_compare.py
Dependency: matplotlib (optional; if missing, prints data summary and exits)
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "fig4_asan_compare.png")

# sec 10.7 raw data (time ms, RSS MB): (load, fat-on, fat-off, asan, asan RSS)
LOADS = [
    ("ptr_traverse", 381.9, 263.1, 134.0, 5.0),
    ("alloc_dense", 428.6, 330.6, 156.1, 131.5),
    ("mixed", 372.2, 135.9, 29.2, 5.9),
]
FAT_PEAK_RSS = 35.9  # fat-pointer side peak RSS (shared across loads, sec 10.7)


def main() -> int:
    names = [l[0] for l in LOADS]
    fat_on = [l[1] for l in LOADS]
    fat_off = [l[2] for l in LOADS]
    asan_t = [l[3] for l in LOADS]
    asan_rss = [l[4] for l in LOADS]
    ratios = [asan / fat for asan, fat in zip(asan_t, fat_on)]

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:  # matplotlib optional
        print("matplotlib not installed; printing data summary (PNG not generated)")
        print("install: pip install matplotlib")
        print(f"{'load':<14}{'fat-on(ms)':>12}{'fat-off(ms)':>12}{'asan(ms)':>10}"
              f"{'asan/fat-on':>12}{'asan RSS(MB)':>13}")
        for n, a, b, c, r, rss in zip(names, fat_on, fat_off, asan_t, ratios, asan_rss):
            print(f"{n:<14}{a:>12.1f}{b:>12.1f}{c:>10.1f}{r:>12.2f}{rss:>13.1f}")
        return 1

    x = [0, 1, 2]
    width = 0.25

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), gridspec_kw={"width_ratios": [2.2, 1]})

    # left: time comparison (log scale, asan far below fat side)
    ax1.bar([p - width for p in x], fat_on, width, label="fat-pointer checks on",
            color="#3182bd")
    ax1.bar(x, fat_off, width, label="fat-pointer checks off", color="#9ecae1")
    ax1.bar([p + width for p in x], asan_t, width, label="ASan", color="#de2d26")
    ax1.set_yscale("log")
    ax1.set_xticks(x)
    ax1.set_xticklabels(names)
    ax1.set_ylabel("time (ms, log scale)")
    ax1.set_title("Time comparison (sec 10.7 historical)")
    for p, a, r in zip(x, asan_t, ratios):
        ax1.text(p + width, a * 1.1, f"{r:.2f}x", ha="center", va="bottom", fontsize=8,
                 color="#de2d26", fontweight="bold")
    ax1.legend(fontsize=8)

    # right: RSS comparison (asan vs fat side peak)
    ax2.bar([p - width / 2 for p in x], asan_rss, width, label="ASan peak RSS",
            color="#de2d26", alpha=0.7)
    for p in x:
        ax2.bar(p + width / 2, FAT_PEAK_RSS, width, label="fat side peak RSS" if p == 0 else None,
                color="#3182bd", alpha=0.7)
    ax2.set_xticks(x)
    ax2.set_xticklabels(names)
    ax2.set_ylabel("peak RSS (MB)")
    ax2.set_title("RSS comparison (sec 10.7)")
    ax2.legend(fontsize=8)

    fig.suptitle("ASan comparison (clang -O2 -fsanitize=address, "
                 "ASAN_OPTIONS=detect_leaks=0)\n"
                 "WARNING: historical benchmark, not current suite; loads removed 2026-08; "
                 "ASan covers spatial safety only (no temporal/UAF)",
                 fontsize=11, fontweight="bold")

    fig.text(0.01, 0.01,
             "Data: docs/security-code.md sec 10.7 (L322, measured 2026-08-14, "
             "archived in build/bench/results.md)\n"
             "Note: ASan time is 0.35x/0.36x/0.08x of fat-check-on; ASan peak RSS "
             "5.0/131.5/5.9 MB vs\n"
             "      fat side 35.9 MB. Not directly extrapolatable to the current shootout "
             "suite (assessment.md sec 5.1)",
             fontsize=7, color="gray")

    fig.tight_layout(rect=[0, 0.06, 1, 0.93])
    fig.savefig(OUT_PATH, dpi=200)
    print(f"OK: {OUT_PATH} generated (3 historical loads; ASan/fat-on: "
          + " / ".join(f"{r:.2f}x" for r in ratios) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())

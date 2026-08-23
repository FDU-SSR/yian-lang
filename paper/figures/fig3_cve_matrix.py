#!/usr/bin/env python3
"""fig3_cve_matrix.py — CVE interception matrix (38 CVE x 12 mechanisms)

Output: paper/figures/fig3_cve_matrix.png

Data source: tests/fat_cve/docs/MECHANISMS.md (mechanism-CVE attribution table,
「完整归属明细」, 38 rows) — parsed live at runtime, so the figure stays in sync
with the attribution table.

Show: one row per CVE, one column per mechanism; filled cell = the CVE is
intercepted by that mechanism. Mechanisms 1/2/3/7/9/10/11/12 have no directly
attributed CVE (carrier mechanism or compile-time form); left empty.

Run: python3 paper/figures/fig3_cve_matrix.py
Dependency: matplotlib (optional; if missing, prints attribution summary and exits)
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# locate MECHANISMS.md relative to repo root (script may run from any cwd)
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
MECH_PATH = os.path.join(REPO_ROOT, "tests", "fat_cve", "docs", "MECHANISMS.md")
OUT_PATH = os.path.join(HERE, "fig3_cve_matrix.png")

MECH_NAMES = [
    "1 rep", "2 Gen/key", "3 lock", "4 in_bounds", "5 live",
    "6 is_heap", "7 is_raw", "8 frame rekey", "9 cmp", "10 ptrdiff",
    "11 ZST", "12 restricted",
]


def parse_attribution(path: str) -> list[tuple[str, int, int]]:
    """Parse the full-attribution table -> [(CVE-ID, primary, secondary)]."""
    out: list[tuple[str, int, int]] = []
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    in_table = False
    for line in lines:
        if "完整归属明细" in line:
            in_table = True
            continue
        if in_table and line.startswith("| CVE-"):
            cols = [c.strip() for c in line.split("|")]
            cve = cols[1]
            if not re.fullmatch(r"CVE-\d{4}-\d{4,5}", cve):
                continue  # skip table header row "| CVE-ID | ..."
            mech = cols[3]
            m = re.search(r"(\d+)", mech)
            primary = int(m.group(1)) if m else 0
            secondary = 0
            m2 = re.search(r"\+(\d+)", mech)
            if m2:
                secondary = int(m2.group(1))
            out.append((cve, primary, secondary))
    return out


def main() -> int:
    attrs = parse_attribution(MECH_PATH)
    assert len(attrs) == 38, f"期望 38 个 CVE 归属, 实际 {len(attrs)}"

    cves = [a[0] for a in attrs]
    primary = [a[1] for a in attrs]
    secondary = [a[2] for a in attrs]

    # distribution check (matches MECHANISMS.md totals)
    from collections import Counter

    dist = Counter(primary)
    assert dist[4] == 25 and dist[5] == 10 and dist[6] == 1 and dist[8] == 2, f"distribution mismatch: {dict(dist)}"
    sec_dist = Counter(s for s in secondary if s)
    # 2 frame-lock cases (CVE-2023-26463/CVE-2026-26399) carry +5 live; 1 UAF carries +2 Gen/key
    assert sec_dist[5] == 2 and sec_dist[2] == 1, f"secondary mismatch: {dict(sec_dist)}"

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:  # matplotlib optional
        print("matplotlib not installed; printing attribution summary (PNG not generated)")
        print("install: pip install matplotlib")
        print(f"CVE count: {len(cves)}; distribution: {dict(dist)}")
        for c, p, s in zip(cves, primary, secondary):
            print(f"{c:<20} primary mech {p:<12}" + (f" (+{s})" if s else ""))
        return 1

    n_mech = len(MECH_NAMES)
    n_cve = len(cves)

    # matrix: rows = mechanisms, cols = CVEs
    data = [[0] * n_cve for _ in range(n_mech)]
    for j, (p, s) in enumerate(zip(primary, secondary)):
        data[p - 1][j] = 1
        if s:
            data[s - 1][j] = 2  # secondary in a different color

    fig, ax = plt.subplots(figsize=(20, 6))
    cmap = __import__("matplotlib").colors.ListedColormap(
        ["#f7f7f7", "#3182bd", "#fdae6b"]  # none / primary / secondary
    )
    ax.imshow(data, aspect="auto", cmap=cmap, vmin=0, vmax=2)

    ax.set_xticks(range(n_cve))
    ax.set_xticklabels(cves, rotation=90, fontsize=6)
    ax.set_yticks(range(n_mech))
    ax.set_yticklabels(MECH_NAMES, fontsize=8)

    # grid lines
    ax.set_xticks([x - 0.5 for x in range(n_cve + 1)], minor=True)
    ax.set_yticks([y - 0.5 for y in range(n_mech + 1)], minor=True)
    ax.grid(which="minor", color="black", linewidth=0.4)
    ax.tick_params(which="minor", length=0)

    ax.set_title("CVE interception matrix: 38 CVEs x 12 mechanisms "
                 "(tests/fat_cve, MECHANISMS.md backfilled adb102a)\n"
                 f"attribution: 4 in_bounds 25 . 5 live 10 . 6 is_heap 1 . "
                 f"8 frame rekey 2 (+2 secondary)",
                 fontsize=11)

    import matplotlib.patches as mpatches

    ax.legend(handles=[
        mpatches.Patch(color="#3182bd", label="primary attribution"),
        mpatches.Patch(color="#fdae6b", label="secondary (live check point)"),
        mpatches.Patch(color="#f7f7f7", label="no direct attribution"),
    ], loc="lower right", fontsize=8)

    fig.text(0.01, 0.01,
             "Data: tests/fat_cve/docs/MECHANISMS.md (full attribution table, "
             "backfilled in adb102a)\n"
             "Note: mechanisms 1/2/3 are carriers (no standalone trigger), 7/9/10/11/12 "
             "have no in-suite CVE;\n"
             "      each attribution is traceable to a negative .an and a security.md rule",
             fontsize=7, color="gray")

    fig.tight_layout(rect=[0, 0.04, 1, 1])
    fig.savefig(OUT_PATH, dpi=200)
    print(f"OK: {OUT_PATH} generated ({len(cves)} CVEs x {n_mech} mechanisms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

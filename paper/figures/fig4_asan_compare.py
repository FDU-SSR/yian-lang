#!/usr/bin/env python3
"""fig4_asan_compare.py — ASan cross-compare (current 14-benchmark suite)

Output: paper/figures/fig4_asan_compare.pdf (or argv[1])

Data source: paper/data/asan-results.md (frozen snapshot of
build/bench/asan-results.md, measured 2026-08-24, 14 benchmarks x 4 legs
[C plain / C ASan main / C ASan sensitivity (binarytree only) / .an check],
5 runs each, taskset -c 4).

Three calibers (per-benchmark, medians):
  (i)   C ASan / C plain      - ASan's own overhead
  (ii)  C ASan / .an check    - ASan vs fat-pointer (cross-compiler)
  (iii) .an check / C plain   - fat-pointer full stack vs uninstrumented C

Assertions (fail loudly if data is inconsistent or edited):
  - exactly 14 benchmarks, each with legs {C plain, C ASan main, .an check}
  - recomputed per-benchmark ratios == ratio table in the data file (+-0.01)
  - recomputed geometric means == stated 1.59x / 0.73x / 2.19x (+-0.01)
  - binarytree sensitivity row present (RSS main 650.0 / sens 168.9 MB)

Run: python3 paper/figures/fig4_asan_compare.py
Dependency: matplotlib (optional; if missing, prints data summary and exits)
"""
from __future__ import annotations

import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "..", "data", "asan-results.md")
OUT_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "fig4_asan_compare.pdf")

LEG_PLAIN = "C plain"
LEG_ASAN = "C ASan main"
LEG_SENS = "C ASan sens"
LEG_AN = ".an check"
LEGS = [LEG_PLAIN, LEG_ASAN, LEG_AN]

GEO_MEAN_EXPECTED = {"i": 1.59, "ii": 0.73, "iii": 2.19}  # stated in data file


def _strip_markup(s: str) -> str:
    return s.strip().replace("**", "").replace("×", "")


def parse_data(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    # section 1: per-leg table
    # | 基准 | 腿 | 时间中位(ms) | IQR(ms) | min–max(ms) | CV | RSS中位(MB) | RSS IQR(MB) | 样本数 |
    legs: dict[str, dict[str, dict]] = {}  # bench -> leg -> {time, iqr, rss, cv, n}
    in_sec1 = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("## 1)"):
            in_sec1 = True
            continue
        if in_sec1:
            if s.startswith("## 2)"):
                in_sec1 = False
                continue
            if not s.startswith("|") or s.startswith("|---"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) != 9 or cells[0].startswith("基准"):
                continue
            bench, leg = cells[0], cells[1]
            legs.setdefault(bench, {})[leg] = {
                "time": float(cells[2]),
                "iqr": float(cells[3]),
                "rss": float(cells[6]),
                "cv": float(cells[5]),
                "n": int(cells[8]),
            }

    # section 2: per-benchmark ratio table + geometric means
    # | 基准 | 口径(i) C ASan/C plain | 口径(ii) C ASan/.an check | 口径(iii) .an check/C plain |
    ratios: dict[str, dict[str, float]] = {}  # bench -> caliber -> ratio
    geo: dict[str, float] = {}
    in_sec2 = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("## 2)"):
            in_sec2 = True
            continue
        if in_sec2:
            if s.startswith("## 3)"):
                in_sec2 = False
                continue
            if not s.startswith("|") or s.startswith("|---"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) != 4:
                continue
            name = cells[0]
            try:
                vals = {k: float(_strip_markup(v))
                        for k, v in zip(("i", "ii", "iii"), cells[1:])}
            except ValueError:  # header / non-numeric row
                continue
            if _strip_markup(name).startswith("几何平均"):
                geo = vals
            else:
                ratios[name] = vals

    # section 3: sensitivity rows (binarytree only)
    # | 腿 | 时间中位(ms) | RSS中位(MB) | 样本数 |
    sens: dict[str, tuple[float, float, int]] = {}
    in_sec3 = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("## 3)"):
            in_sec3 = True
            continue
        if in_sec3:
            if s.startswith("## 4)"):
                in_sec3 = False
                continue
            if not s.startswith("|") or s.startswith("|---"):
                continue
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) != 4 or cells[0].startswith("腿"):
                continue
            sens[cells[0]] = (float(cells[1]), float(cells[2]), int(cells[3]))

    return {"legs": legs, "ratios": ratios, "geo": geo, "sens": sens}


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def verify(data: dict) -> None:
    legs, ratios, geo, sens = data["legs"], data["ratios"], data["geo"], data["sens"]

    benches = sorted(legs.keys())
    _assert(len(benches) == 14, f"expect 14 benchmarks, got {len(benches)}: {benches}")

    geo_i = []
    geo_ii = []
    geo_iii = []
    for b in benches:
        for leg in LEGS:
            _assert(leg in legs[b],
                    f"{b}: missing leg '{leg}' (have {sorted(legs[b])})")
        plain, asan, an = legs[b][LEG_PLAIN], legs[b][LEG_ASAN], legs[b][LEG_AN]
        r_i = asan["time"] / plain["time"]
        r_ii = asan["time"] / an["time"]
        r_iii = an["time"] / plain["time"]
        _assert(b in ratios, f"{b}: missing ratio row in section 2")
        _assert(abs(r_i - ratios[b]["i"]) <= 0.01,
                f"{b}: caliber(i) {r_i:.3f} != table {ratios[b]['i']}")
        _assert(abs(r_ii - ratios[b]["ii"]) <= 0.01,
                f"{b}: caliber(ii) {r_ii:.3f} != table {ratios[b]['ii']}")
        _assert(abs(r_iii - ratios[b]["iii"]) <= 0.01,
                f"{b}: caliber(iii) {r_iii:.3f} != table {ratios[b]['iii']}")
        geo_i.append(r_i)
        geo_ii.append(r_ii)
        geo_iii.append(r_iii)

    for key, xs in (("i", geo_i), ("ii", geo_ii), ("iii", geo_iii)):
        gm = math.exp(sum(math.log(x) for x in xs) / len(xs))
        _assert(abs(gm - geo[key]) <= 0.01,
                f"geo mean caliber({key}): computed {gm:.3f} != stated {geo[key]}")
        _assert(abs(gm - GEO_MEAN_EXPECTED[key]) <= 0.01,
                f"geo mean caliber({key}): computed {gm:.3f} != expected {GEO_MEAN_EXPECTED[key]}")

    _assert(LEG_SENS in legs["binarytree"],
            "binarytree missing sensitivity leg 'C ASan sens'")
    main_rss = legs["binarytree"][LEG_ASAN]["rss"]
    sens_rss = legs["binarytree"][LEG_SENS]["rss"]
    _assert(abs(main_rss - 650.0) <= 0.1, f"binarytree main RSS {main_rss} != 650.0")
    _assert(abs(sens_rss - 168.9) <= 0.1, f"binarytree sens RSS {sens_rss} != 168.9")
    _assert(any("main" in k for k in sens), "section 3 sensitivity table missing")
    _assert(any("sens" in k for k in sens), "section 3 sensitivity table missing")


def main() -> int:
    data = parse_data(os.path.join(HERE, "..", "data", "asan-results.md"))
    verify(data)
    benches = sorted(data["legs"].keys())

    plain_t = [data["legs"][b][LEG_PLAIN]["time"] for b in benches]
    asan_t = [data["legs"][b][LEG_ASAN]["time"] for b in benches]
    an_t = [data["legs"][b][LEG_AN]["time"] for b in benches]
    r_asan = [a / p for a, p in zip(asan_t, plain_t)]  # caliber (i)
    r_an = [a / p for a, p in zip(an_t, plain_t)]  # caliber (iii)
    plain_rss = [data["legs"][b][LEG_PLAIN]["rss"] for b in benches]
    asan_rss = [data["legs"][b][LEG_ASAN]["rss"] for b in benches]
    an_rss = [data["legs"][b][LEG_AN]["rss"] for b in benches]

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:  # matplotlib optional
        print("matplotlib not installed; printing data summary (PNG not generated)")
        print("install: pip install matplotlib")
        print(f"{'bench':<14}{'C plain(ms)':>12}{'C ASan(ms)':>12}"
              f"{'.an check(ms)':>14}{'C ASan/C plain':>15}{'.an/C plain':>12}"
              f"{'RSS .an(MB)':>12}")
        for b, p, a, n, r1, r2, r in zip(benches, plain_t, asan_t, an_t,
                                         r_asan, r_an, an_rss):
            print(f"{b:<14}{p:>12.1f}{a:>12.1f}{n:>14.1f}{r1:>15.2f}{r2:>12.2f}"
                  f"{r:>12.1f}")
        print("assertions passed: 14 benchmarks, ratios and geo means consistent")
        return 1

    x = range(len(benches))
    time_width = 0.32
    rss_width = 0.26

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(17, 6.5),
                                   gridspec_kw={"width_ratios": [1.5, 1]})

    # left: time ratio vs C plain (baseline 1.0), log scale
    ax1.axhline(1.0, color="black", linewidth=0.8, linestyle="--", alpha=0.6,
                label="C plain baseline (1.0)")
    ax1.bar([p - time_width/2 for p in x], r_asan, time_width,
            label="C ASan (clang -O2 -fsanitize=address)",
            color="#bdbdbd", edgecolor="black", linewidth=.6, hatch="///")
    ax1.bar([p + time_width/2 for p in x], r_an, time_width,
            label="SecL check (-O3, fat pointers)", color="#4d4d4d",
            edgecolor="black", linewidth=.6, hatch="xx")
    ax1.set_yscale("log")
    ax1.set_ylim(0.03, max(r_an) * 1.6)  # headroom for top annotations (nbody 27.79x)
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(benches, rotation=45, ha="right", fontsize=8)
    ax1.set_ylabel("time ratio vs C plain (log scale)")
    for p, r in zip(x, r_an):
        if r >= 2.0 or r <= 0.8:
            ax1.text(p + time_width/2, r * 1.12, f"{r:.2f}",
                     ha="center", va="bottom", fontsize=6.5,
                     color="black", fontweight="bold")
    for p, r in zip(x, r_asan):
        if r >= 2.0 or r <= 0.8:
            ax1.text(p - time_width/2, r * 1.12, f"{r:.2f}",
                     ha="center", va="bottom", fontsize=6.5,
                     color="black")
    ax1.legend(fontsize=8, loc="upper left", frameon=False)

    # right: peak RSS (MB), log scale
    ax2.bar([p - rss_width for p in x], plain_rss, rss_width, label="C plain",
            color="#ffffff", edgecolor="black", linewidth=.6)
    ax2.bar(x, asan_rss, rss_width, label="C ASan", color="#bdbdbd",
            edgecolor="black", linewidth=.6, hatch="///")
    ax2.bar([p + rss_width for p in x], an_rss, rss_width, label="SecL check",
            color="#4d4d4d", edgecolor="black", linewidth=.6, hatch="xx")
    ax2.set_yscale("log")
    ax2.set_xticks(list(x))
    ax2.set_xticklabels(benches, rotation=45, ha="right", fontsize=8)
    ax2.set_ylabel("peak RSS (MB, log scale)")
    ax2.legend(fontsize=8, loc="upper left", frameon=False)

    fig.tight_layout()
    fig.savefig(OUT_PATH, bbox_inches="tight")
    print(f"OK: {OUT_PATH} generated (14 benchmarks; assertions passed: "
          f"14 legs x3, ratios & geo means 1.59x/0.73x/2.19x consistent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

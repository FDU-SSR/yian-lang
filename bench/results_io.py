"""Plain numeric performance tables, without measurement metadata."""

from __future__ import annotations

import csv
import math
import os
import statistics
import tempfile
from pathlib import Path

FIELDS = (
    "benchmark", "state", "samples", "min_ms", "median_ms", "iqr_ms", "max_ms",
    "cv_pct", "peak_rss_kb", "median_rss_kb", "min_over_c", "min_over_raw",
    "median_over_c", "median_over_yian",
)


def make_record(
    benchmark: str, state: str, samples_ms: list[float], samples_rss_kb: list[float],
) -> dict[str, object]:
    """Keep ordered observations and compute performance statistics."""
    if not samples_ms or len(samples_ms) != len(samples_rss_kb):
        raise ValueError(f"{benchmark}/{state}: missing or mismatched time/RSS samples")
    if any(not math.isfinite(value) or value <= 0 for value in samples_ms):
        raise ValueError(f"{benchmark}/{state}: invalid time")
    if any(not math.isfinite(value) or value < 0 for value in samples_rss_kb):
        raise ValueError(f"{benchmark}/{state}: invalid RSS")
    try:
        quartiles = statistics.quantiles(samples_ms, n=4, method="exclusive")
        iqr = quartiles[2] - quartiles[0]
    except statistics.StatisticsError:
        iqr = max(samples_ms) - min(samples_ms)
    cv = statistics.stdev(samples_ms) / statistics.fmean(samples_ms) * 100 if len(samples_ms) > 1 else 0.0
    record: dict[str, object] = dict.fromkeys(FIELDS)
    record.update(benchmark=benchmark, state=state, samples=len(samples_ms),
                  samples_ms=list(samples_ms), samples_rss_kb=list(samples_rss_kb),
                  min_ms=min(samples_ms), median_ms=statistics.median(samples_ms), iqr_ms=iqr,
                  max_ms=max(samples_ms), cv_pct=cv, peak_rss_kb=max(samples_rss_kb),
                  median_rss_kb=statistics.median(samples_rss_kb))
    return record


def write_csv(path: Path, records: list[dict[str, object]]) -> None:
    """Write scalar statistics, numbered samples and aggregate ratios only.

    LIBYAML is a standalone real-project workload. Its rows stay in the table
    for direct inspection but are excluded from every geomean aggregate.
    """
    rows = [dict(record) for record in records]
    groups = {}
    seen = set()
    for row in rows:
        if set(row) - set(FIELDS) - {"samples_ms", "samples_rss_kb"}:
            raise ValueError("performance CSV cannot contain extra fields")
        for field in FIELDS[2:]:
            value = row.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError("performance columns must be numeric")
        if not isinstance(row["samples"], int) or isinstance(row["samples"], bool):
            raise ValueError("sample count must be an integer")
        key = row["benchmark"], row["state"]
        if key in seen or row["benchmark"] == "geomean":
            raise ValueError("duplicate or reserved benchmark/state")
        seen.add(key)
        groups.setdefault(row["benchmark"], {})[row["state"]] = row
    for states in groups.values():
        for row in states.values():
            for column, baseline, statistic in (
                ("min_over_c", "c", "min_ms"), ("min_over_raw", "raw", "min_ms"),
                ("median_over_c", "c_plain", "median_ms"), ("median_over_yian", "an_check", "median_ms"),
            ):
                if baseline in states:
                    row[column] = row[statistic] / states[baseline][statistic]
    aggregate_rows = [row for row in rows if not str(row["benchmark"]).startswith("LIBYAML/")]
    count = max((len(row["samples_ms"]) for row in rows), default=0)
    fields = list(FIELDS) + [name for i in range(1, count + 1) for name in (f"time_ms_{i}", f"rss_kb_{i}")]
    output = []
    for row in rows:
        ms, kb = row.pop("samples_ms"), row.pop("samples_rss_kb")
        if len(ms) != len(kb) or len(ms) != row["samples"]:
            raise ValueError("mismatched performance samples")
        values = {key: round(value, 6) if isinstance(value, float) else value for key, value in row.items()}
        for i, (time_ms, rss_kb) in enumerate(zip(ms, kb), 1):
            values[f"time_ms_{i}"] = time_ms
            values[f"rss_kb_{i}"] = rss_kb
        output.append(values)
    for state in dict.fromkeys(row["state"] for row in rows):
        aggregate = {"benchmark": "geomean", "state": state}
        for field in FIELDS[10:]:
            values = [row[field] for row in aggregate_rows if row["state"] == state and row.get(field) is not None]
            if values:
                aggregate[field] = round(statistics.geometric_mean(values), 6)
        if len(aggregate) > 2:
            output.append(aggregate)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(output)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

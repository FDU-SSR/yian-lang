#!/usr/bin/env python3
"""bench_common.py — 三态基准 (C / raw / fat) 两侧脚本共享的规格.

`bench/c/*.c` 是自写的 C 参考实现, 与 `bench/shootout/*.an` 同算法、同规模。本模块
集中两件必须两边一致的信息:

- `argv`: 传给 C 二进制的参数。缺省是空 (跑参考自身的默认规模); `cd` 与 `richards`
  的 C 参考默认只跑 1 轮, 而 `.an` 跑 80 / 2400 轮, 只有显式传参才与 `.an` 同工作量。
- `check`: C 侧语义权威值——判断 `(退出码, stdout)` 是否等于该基准的权威结果。`.an`
  基准在内部断言同一组值, 因此 C 与 YIAN 两侧跑的是同一件事。

使用方: `bench_three_way.py` (测量时校验 C 基线没有跑偏) 与 `verify_bench_c.py`
(独立校验 C 参考实现)。规模与断言值的对齐依据写在各 `.an`/`.c` 的头部注释里。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

Check = Callable[[int, str], bool]


@dataclass(frozen=True)
class CBenchSpec:
    """一个基准的 C 侧运行参数与语义权威值。"""

    argv: list[str]
    check: Check
    expect: str


C_SPECS: dict[str, CBenchSpec] = {
    "binarytree": CBenchSpec([], lambda rc, out: rc == 0 and int(out.strip()) % 256 == 176,
                             "rc==0, check%256==176"),
    "bounce": CBenchSpec([], lambda rc, out: rc == 0 and "368285073" in out,
                         'rc==0, stdout contains "368285073"'),
    "cd": CBenchSpec(["100", "80"], lambda rc, out: rc == 0 and out.strip() == "344400",
                     'rc==0, stdout=="344400" (100 架 200 帧 × 80 轮, 与 cd.an 的 ITER 一致)'),
    "deltablue": CBenchSpec([], lambda rc, out: rc == 0 and out.strip() == "4993240000",
                            'rc==0, stdout=="4993240000" (N=100 × ITER=14000; 每趟 356660)'),
    "fann": CBenchSpec([], lambda rc, out: rc == 51 and "51" in out,
                       'rc==51, stdout contains "51"'),
    "fasta": CBenchSpec([], lambda rc, out: rc == 0 and "-71" in out,
                        'rc==0, stdout contains "-71"'),
    "havlak": CBenchSpec([], lambda rc, out: rc == 0 and out.strip() == "1605 5213",
                         'rc==0, stdout=="1605 5213" (loops × nodes)'),
    "json": CBenchSpec([], lambda rc, out: rc == 0 and "5869103028000" in out,
                       'rc==0, stdout contains "5869103028000" (ops/成员/字符数)'),
    "list": CBenchSpec([], lambda rc, out: rc == 0, "rc==0"),
    "mand": CBenchSpec([], lambda rc, out: rc == 0 and "126" in out,
                       'rc==0, stdout contains "126"'),
    "nbody": CBenchSpec([], lambda rc, out: rc == 0 and "-1" in out,
                        'rc==0, stdout contains "-1"'),
    "permute": CBenchSpec([], lambda rc, out: rc == 0 and "823059745" in out,
                          'rc==0, stdout contains "823059745"'),
    "queen": CBenchSpec([], lambda rc, out: rc == 0, "rc==0"),
    "revcomp": CBenchSpec([], lambda rc, out: rc == 0 and "128" in out,
                          'rc==0, stdout contains "128" (Checksum:)'),
    "richards": CBenchSpec(["2400"], lambda rc, out: rc == 0 and out.strip() == "55790400 22312800",
                           'rc==0, stdout=="55790400 22312800" (23246/9297 × 2400 轮, 与 richards.an 的 ITER 一致)'),
    "sieve": CBenchSpec([], lambda rc, out: rc == 0, "rc==0"),
    "spectralnorm": CBenchSpec([], lambda rc, out: rc == 0 and "78" in out,
                               'rc==0, stdout contains "78"'),
    "storage": CBenchSpec([], lambda rc, out: rc == 0 and "21523360" in out,
                          'rc==0, stdout contains "21523360"'),
    "towers": CBenchSpec([], lambda rc, out: rc == 0, "rc==0"),
}

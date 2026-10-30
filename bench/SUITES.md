# Benchmark sources and licenses

This file registers the benchmarks actually run in the repository and their
sources. Sets, scale profiles, run protocol, and result files are documented in
[`bench/README.md`](README.md).

| Source | Directory | Members | License and source notes |
| --- | --- | ---: | --- |
| Are We Fast Yet (`smarr/are-we-fast-yet`) | `bench/AWFY/` | 14 | Upstream sources and licenses vary by benchmark: some implementations come from the Benchmarks Game (Revised BSD); the Java implementations of Richards and DeltaBlue carry the MIT license, and their earlier sources appear in the header comments of the corresponding source files. The AWFY C reference implementations are written in this repository. |
| The Computer Language Benchmarks Game | `bench/BG/` | 5 | Revised BSD; source file header comments record the specific source and copyright information. |
| Allocator workloads written in this repository | `bench/ALLOC/` | 4 | Repository code; fat/raw two-state comparison against libc `malloc`/`free`. |
| Mechanism microbenchmarks written in this repository | `bench/fatptr/` | 3 | Repository code; compares representation size, argument passing, copying, and pointer-chasing costs. |
| C reference implementations for the corresponding benchmarks | `bench/AWFY/c/`, `bench/BG/c/`, `bench/fatptr/c/` | 22 | Written in this repository; used to align workload and check results; criteria are in the [C → YIAN specification](../docs/c-to-yian.md), and current differences and local evidence are in the [review report](../docs/c-to-yian-review.md). |

Upstream home pages:

- [Are We Fast Yet](https://github.com/smarr/are-we-fast-yet)
- [The Computer Language Benchmarks Game](https://benchmarksgame-team.pages.debian.net/benchmarksgame/)
- [Benchmarks Game license](https://benchmarksgame-team.pages.debian.net/benchmarksgame/license.html)

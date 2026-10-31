# Benchmark sources and licenses

This file registers the benchmark projects included in the repository and their
sources. Sets, scale profiles, run protocol, and result files are documented in
[`bench/README.md`](README.md).

| Source | Directory | Members | License and source notes |
| --- | --- | ---: | --- |
| Are We Fast Yet (`smarr/are-we-fast-yet`) | `bench/AWFY/<name>/` | 14 | Upstream algorithm provenance and licenses vary by benchmark. The C references are repository projects; the Java implementations of Richards and DeltaBlue carry MIT notices. |
| The Computer Language Benchmarks Game | `bench/BG/<name>/` | 10 | Revised BSD. Each project records its selected official C source, license, input/output fixtures, function mapping, local portability patches, and hashes in `README.md` and `provenance.json`. |
| Allocator workloads written in this repository | `bench/ALLOC/<name>/` | 4 | Repository code; fat/raw two-state comparison against libc `malloc`/`free`, without a C reference. |
| Mechanism microbenchmarks written in this repository | `bench/fatptr/<name>/` | 3 | Repository code; C/YIAN pairs compare representation size, argument passing, copying, and pointer-chasing costs. |
| Paired C/YIAN projects | `bench/AWFY/<name>/c/`, `bench/BG/<name>/c/`, `bench/fatptr/<name>/c/` | 27 | The AWFY/fatptr references are repository C projects; BG references retain the selected official sources and disclose any small C portability patch. Workload criteria are in the [C → YIAN specification](../docs/c-to-yian.md); current translation gaps and evidence limits are in the [review report](../docs/c-to-yian-review.md). |

The `full` three-state set contains 27 pairs (14 AWFY, 10 BG, 3 fatptr), and
`sets/bg.json` selects the ten BG projects. The `paper` set remains 14 IDs (9
AWFY and the five original BG IDs). The four `ALLOC` projects are selected only
by the fat/raw allocator runner. GMP 6.3.0 and PCRE2 10.47 are fixed project-local
dependencies for pidigits and regexredux; they are not benchmark projects. Both
libraries use the same static archives in C/raw/fat and are not ASan-instrumented
or covered by YIAN guards. See [`native/README.md`](native/README.md) and each
case's dependency provenance.

Upstream home pages:

- [Are We Fast Yet](https://github.com/smarr/are-we-fast-yet)
- [The Computer Language Benchmarks Game](https://benchmarksgame-team.pages.debian.net/benchmarksgame/)
- [Benchmarks Game license](https://benchmarksgame-team.pages.debian.net/benchmarksgame/license.html)

# 分配器基准 (fat vs raw) — 规模档 `full`

由 `bench/bench_allocator.py` 生成; 分配器基准没有 C 参考, 对照态是 libc `malloc`/`free`。
采样: 每态 7 次取最小墙钟 / 最大峰值 RSS; 绑核: 4; commit: `641657c`; 日期: 2026-09-23

| 基准 | fat 最小 (ms) | raw 最小 (ms) | fat/raw | fat 峰值 RSS (MB) | raw 峰值 RSS (MB) | 输出一致 | 规模 |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| churn_mixed | 24.6 | 59.0 | 0.417 | 1.1 | 1.1 | yes | 默认 |
| churn_single | 22.0 | 47.1 | 0.468 | 1.3 | 1.3 | yes | 默认 |
| grow_free | 24.9 | 158.2 | 0.157 | 18.4 | 17.1 | yes | 默认 |
| grow_varied | 139.2 | 138.9 | 1.002 | 64.4 | 32.9 | yes | 默认 |


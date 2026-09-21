# 分配器基准 (fat vs raw) — 规模档 `full`

由 `bench/bench_allocator.py` 生成; 分配器基准没有 C 参考, 对照态是 libc `malloc`/`free`。
采样: 每态 7 次取最小墙钟 / 最大峰值 RSS; 绑核: 4; commit: `e0c5294`（变体 B + 三处优化器修复；当时为工作树快照）; 日期: 2026-09-21

| 基准 | fat 最小 (ms) | raw 最小 (ms) | fat/raw | fat 峰值 RSS (MB) | raw 峰值 RSS (MB) | 输出一致 | 规模 |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| churn_mixed | 25.9 | 56.2 | 0.460 | 1.1 | 1.1 | yes | 默认 |
| churn_single | 21.6 | 45.2 | 0.478 | 1.3 | 1.3 | yes | 默认 |
| grow_free | 21.0 | 134.1 | 0.157 | 18.4 | 17.1 | yes | 默认 |
| grow_varied | 120.5 | 125.2 | 0.963 | 64.4 | 32.9 | yes | 默认 |


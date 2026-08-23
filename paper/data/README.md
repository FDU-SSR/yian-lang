# 论文数据冻结快照

冻结日期：2026-08-24。当前实现基准提交：afd67de。

| 文件 | 来源 | 内容 |
| --- | --- | --- |
| performance.csv | docs/performance.csv | 14 项三态成本倍率与 RSS 差分 |
| shootout-results.md | build/bench/shootout-results.md | 协议、中位数、IQR、min/max、RSS 和全部原始样本 |
| asan-results.md | build/bench/asan-results.md | 43 条 ASan 对照腿及全部原始样本 |

## 冻结结果

- 三态协议：CPU 4；每态 1 次 warmup + 5 次正式样本；check→nocheck→raw 紧邻。
- 表示/池/锁 nocheck/raw 几何平均 1.13×。
- 检查 check/nocheck 几何平均 1.13×。
- 完整机制 check/raw 几何平均 1.27×，≤1.2× 为 8/14，最大 list 3.79×。
- 主要 RSS 增量：binarytree +256.08MB，storage +2189.53MB。
- ASan 三口径：C ASan/C plain 1.59×，C ASan/SecL check 0.73×，SecL check/C plain 2.19×。
- ASan binarytree quarantine 敏感性：RSS 650.0→168.9MB（−74.0%），时间 −7.2%。

## 解释边界

raw 使用适配后的等价源码和裸指针；nocheck 仍保留胖表示、堆池与锁协议。C plain/ASan 使用
clang -O2，SecL check 使用自身前端和 LLVM/clang -O3。跨编译器倍率不能全部归因于
安全检查。重跑实验后必须整体替换本目录快照并重新执行图表断言。

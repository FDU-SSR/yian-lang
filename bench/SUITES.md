# 评测来源与许可

本文件登记仓库中实际运行的基准及其来源。评测集合、规模档、运行协议和结果文件见
[`bench/README.md`](README.md)。

| 来源 | 目录 | 成员 | 许可与来源说明 |
| --- | --- | ---: | --- |
| Are We Fast Yet（`smarr/are-we-fast-yet`） | `bench/AWFY/` | 14 | 上游来源与许可因基准而异：部分实现来自 Benchmarks Game（Revised BSD）；Richards 和 DeltaBlue 的 Java 实现带 MIT 许可，其更早来源见对应源文件头注释。AWFY 的 C 参考实现由本仓库编写。 |
| The Computer Language Benchmarks Game | `bench/BG/` | 5 | Revised BSD；基准源文件头注释记录具体来源与版权信息。 |
| 本仓库编写的分配器负载 | `bench/ALLOC/` | 4 | 本仓库代码；fat/raw 两态对照 libc `malloc`/`free`。 |
| 本仓库编写的机制微基准 | `bench/fatptr/` | 3 | 本仓库代码；比较表示尺寸、传参、拷贝与指针追逐的成本。 |
| 对应基准的 C 参考实现 | `bench/AWFY/c/`、`bench/BG/c/`、`bench/fatptr/c/` | 22 | 本仓库编写；与 YIAN 版本使用相同的工作量和结果校验。 |

上游主页：

- [Are We Fast Yet](https://github.com/smarr/are-we-fast-yet)
- [The Computer Language Benchmarks Game](https://benchmarksgame-team.pages.debian.net/benchmarksgame/)
- [Benchmarks Game 许可说明](https://benchmarksgame-team.pages.debian.net/benchmarksgame/license.html)

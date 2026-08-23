# references/ — 论文引用清单

`citations.md` 是论文引用清单的冻结副本，来源为 `.omo/notes/citations.md`
（librarian 于 2026-08-07 核实）。

## 排除说明（重要）

原清单共 17 条（12 requested + 1 variant 8b + 4 appended），其中 **3 篇未核实文献已从
本清单剔除**，论文引用不得包含三者：

| 被排除条目 | 标题（声称） | 排除原因 |
|---|---|---|
| 8b | Extensible Metadata for Memory Safety（声称 OOPSLA 2016） | dblp/OpenAlex/ACM DL/web 均无此标题；实际后续为 A4（NDSS 2017） |
| 11 | WatchTower: Fast, Secure Memory Safety（声称 ASPLOS 2018） | 无此标题论文；疑为 Watchdog（ISCA 2012，即 A1） |
| 12 | Buddy: Memory Safety for the C Language（声称 IEEE S&P 2020） | 无此标题论文；疑为 Baggy Bounds Checking（USENIX Security 2009，即 A3） |

本副本仅保留 **14 条 `verified` 引用**（条目 1–10 + 附录 A1–A4）。被排除条目的
best-known metadata 与核查过程保留在源文件 `.omo/notes/citations.md`，如论文需要
引用对应的疑似真实文献（Watchdog / Baggy Bounds），使用本清单中的 A1 / A3 条目。

## 与 assessment.md 的对应

assessment.md §6 数据源表末行声明「排除 3 篇未核实文献（8b Extensible Metadata /
11 WatchTower / 12 Buddy），论文引用清单不得含三者」——本目录即该声明的落地物。

## 更新策略

- 论文引用清单的权威源是 `.omo/notes/citations.md`；如后续补核实文献，更新源文件后
  重新执行排除复制。
- 本副本头部与 Summary 已注明来源、冻结日期（2026-08-23）与排除清单，论文写作时
  直接以此为准。

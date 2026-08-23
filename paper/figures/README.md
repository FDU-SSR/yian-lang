# 论文图表

所有脚本从冻结数据或 CVE 归属表读取输入，先执行断言，再生成矢量 PDF。第一个可选参数可指定
输出路径，便于直接生成到外层 LaTeX 仓库。

| 脚本 | 作用 | 默认输出 | 使用建议 |
| --- | --- | --- | --- |
| `design_overview.py` | 流水线、三级表示、堆/栈生命周期 | `design_overview.pdf` | 正文 |
| `fig1_cost_decomposition.py` | 14 项表示/检查/总成本三态分解 | `fig1_cost_decomposition.pdf` | 正文 |
| `fig3_cve_summary.py` | 38 CVE 主机制 25/10/1/2 汇总 | `fig3_cve_summary.pdf` | 正文 |
| `fig2_check_cost.py` | 独立检查成本 | `fig2_check_cost.pdf` | 候选附录；与主性能图部分重复 |
| `fig3_cve_matrix.py` | 38×12 完整归属矩阵 | `fig3_cve_matrix.pdf` | 候选附录；密度较高 |
| `fig4_asan_compare.py` | C plain/ASan/SecL check 对照 | `fig4_asan_compare.pdf` | 候选附录 |

当前断言：三态总成本 ≤1.2× 为 8/14，检查成本 ≤1.2× 为 10/14；CVE 为 38 个、76 个
成对用例；ASan 三口径几何平均为 1.59×/0.73×/2.19×。

运行示例：

```bash
python paper/figures/fig1_cost_decomposition.py
python paper/figures/design_overview.py /path/to/outer/fig/design.pdf
```

2026-08-24 已人工核对正文候选三张图与 ASan 候选图；最终仍需在 LaTeX 实际双栏/单栏版面
检查字号和标签可读性。

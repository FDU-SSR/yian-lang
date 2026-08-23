# figures/ — 图表导出脚本

本目录存放论文图表的导出脚本（Python，标准库 csv + matplotlib）。每个脚本可独立运行
并产出 PNG 到本目录；数据源路径在脚本头部与图注中注明。

## 脚本清单

| 脚本 | 图 | 数据源 | 产出 |
|---|---|---|---|
| `fig1_cost_decomposition.py` | 三态成本分解堆叠图（14 基准，表示/检查/总） | `../data/performance.csv` | `fig1_cost_decomposition.png` |
| `fig2_check_cost.py` | 检查成本 vs 基准（③/② 列，标注 10/14 ≤1.2×） | `../data/performance.csv` | `fig2_check_cost.png` |
| `fig3_cve_matrix.py` | CVE 拦截矩阵（38 CVE × 12 机制热图） | `tests/fat_cve/docs/MECHANISMS.md` | `fig3_cve_matrix.png` |
| `fig4_asan_compare.py` | ASan 对照（当前套件 14 基准，三口径几何平均 1.58×/0.69×/2.28×，2026-08-24 retest） | `../data/asan-results.md` | `fig4_asan_compare.png` |

## 运行

```bash
cd paper/figures
python3 fig1_cost_decomposition.py
python3 fig2_check_cost.py
python3 fig3_cve_matrix.py
python3 fig4_asan_compare.py
```

`fig3_cve_matrix.py` 从仓库根的 `tests/fat_cve/docs/MECHANISMS.md` 实时解析归属表
（脚本自行定位仓库根），归属分布有断言校验（25/10/1/2 + 2 次级），归属表后续维护时
重跑即可保持图与数据同步。

## matplotlib 依赖（可选）

matplotlib **不强制安装**：脚本运行时若未安装，自动降级为打印数据摘要（表格形式，
数值不变）并提示安装命令，PNG 不产出。装好后重跑即出图。

## 图表验证（待补项，见 ../checklist.md）

- 图 1/2 的数值应逐一与 `../data/performance.csv` 核对（脚本在产出时已断言 14 基准）。
- 图 3 的归属应与 `tests/fat_cve/docs/MECHANISMS.md`「完整归属明细」表人工抽查核对。
- 图 4 的数值为当前套件（2026-08-24 retest，`../data/asan-results.md` 冻结），
  重测后重跑脚本即可同步；旧 §10.7 历史基准（0.35×/0.36×/0.08×）仅在图注中标注
  superseded，不再绘制。

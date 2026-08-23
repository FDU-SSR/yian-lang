# data/ — 论文数据冻结快照

本目录冻结论文引用的性能数据，保证「论文引用的数字」与「仓库当时状态」可追溯、
可复现。**论文引用一律以本目录快照为准**，不直接引用 `docs/` 工作副本。

## 文件

| 文件 | 来源 | 内容 | 权威性 |
|---|---|---|---|
| `performance.csv` | `docs/performance.csv` | 14 基准三态成本分解倍率（表示 ②/①、检查 ③/②、总 ③/①）+ ΔRSS | **权威**：assessment.md §6 指定 |
| `shootout-results.md` | `docs/shootout-results.md` | 14×3 实测矩阵 + IQR + 原始样本附录 + 测量协议 | 备查：2026-08-24 retest 快照（与 performance.csv 同源同会话，数据区逐行一致） |
| `asan-results.md` | `build/bench/asan-results.md` | ASan 交叉对比（14 基准 × 4 腿 × 5 runs + 三口径几何平均 1.58×/0.69×/2.28×） | **权威**：fig4 数据源，assessment.md §6 指定 |

三份文件均在头部加注了冻结说明（来源路径 + 冻结日期 2026-08-24 + 同步机制说明）。

## 冻结策略

- **冻结 = 复制 + 头注**，不手改数据区。`performance.csv` 由
  `python3 scripts/bench_fat.py --suite shootout` 自动同步（合并更新，部分重跑只刷新
  被测基准）；重测后需重新执行本冻结流程。
- 若论文数值需要更新，流程为：重跑 bench_fat 同步 `docs/performance.csv` → 重新
  复制冻结 → 更新本目录 `performance.csv` 头注的冻结日期 → 重新运行 `fig1/fig2`
  产出新图。

## 数据一致性比对

冻结后应立即核对数据区与源文件一致。比对命令（忽略 `#`/`>` 头注与空行）：

```bash
# performance.csv: 数据区（非 # 开头行）逐行比对
diff <(grep -v '^#' docs/performance.csv) <(grep -v '^#' paper/data/performance.csv) \
  && echo "OK: data/performance.csv 数据区与 docs/performance.csv 一致"

# shootout-results.md: 去冻结头注（首行到 '---' 分隔线，含）后与源文件逐行比对
# （docs/ 源文件无 '---' 分隔线——头注只存在于冻结副本，故以 docs 全文为基准）
diff docs/shootout-results.md \
     <(sed -n '/^---$/,$p' paper/data/shootout-results.md | tail -n +2) \
  && echo "OK: data/shootout-results.md 正文与 docs/shootout-results.md 一致"
```

`grep -v '^#'` 会同时跳过数据区的 `#` 注释行（performance.csv 头部本来就是注释，
需确认比对时数据行数相等）。更严格的数值校验见脚本：

```bash
python3 - <<'EOF'
import csv
def rows(p):
    with open(p, newline='') as f:
        return [r for r in csv.reader(f) if r and not r[0].lstrip().startswith('#')]
a, b = rows('docs/performance.csv'), rows('paper/data/performance.csv')
assert a == b, f"diff: docs {len(a)} rows vs paper {len(b)} rows"
print(f"OK: {len(a)-1} 数据行数值与 docs/performance.csv 完全一致")
EOF
```

## 数值速览（冻结时点，2026-08-24）

- 总成本倍率（③/①）≤1.2× 恰好 **7/14**（约半数）；≥1.4× 共 5 个（queen 1.55 /
  revcomp 1.61 / storage 1.52 / list 3.46 / towers 1.71）。
- 检查成本倍率（③/②）≤1.2× 为 **10/14**（独立于总成本的事实，勿混淆）。
- ΔRSS 非零项：binarytree +127.97 MB、storage +1751.75 MB、queen +0.66 MB。
- ASan 三口径几何平均：1.58×（ASan 自身）/ 0.69×（ASan vs 胖指针）/ 2.28×（胖指针 vs C plain）。
- 数据源追溯见 `../assessment.md` §6 数据源与追溯表。

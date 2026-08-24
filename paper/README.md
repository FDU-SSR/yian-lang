# paper/ — SecL 论文材料

本目录保存论文重写使用的冻结数据、图表脚本、引用核验和验收清单。实现与证明的权威说明分别
位于 docs/security-code.md 和 docs/security.md。

## 当前结论

- 正确性：general 241/241、fat 84/84、raw 三态 27/27、38 CVE 成对用例 76/76。
- 表示：T* 40B、T[]/str 32B、T& 24B。
- 帧锁已从普通栈帧移到固定地址的独立影子栈；容量耗尽时 trap。
- `data/` 中的三态与 ASan 数字是帧锁改动前的历史快照，不能用于当前
  实现的性能结论。必须在指定实验机器上完成全量重测后再更新论文。
- 安全论证：O-1--O-5 完整纸面证明，尚未机械化。

## 目录

| 路径 | 内容 |
| --- | --- |
| assessment.md | 当前实现、数据和论文风险评估 |
| data/ | 三态与 ASan 的冻结报告和原始样本 |
| figures/ | 数据驱动图表脚本；候选图不要求全部入正文 |
| references/ | 已核实引用及排除清单 |
| checklist.md | 重写和最终验收状态 |

## 使用约束

论文只能引用 data/ 中当前快照；重跑实验后必须同步快照和图表断言。必须披露单线程、FFI/
ABI、标准库 TCB、元数据防伪、帧级失效、堆池驻留/碎片和 benchmark 缩放。历史 24B 统一表示、
30 CVE、旧 ASan 三负载和旧性能倍率不得进入当前论文。

## 完整重测

先在指定实验机器上检查机器记录和 SHA-256 指纹：

```bash
YIAN_PYTHON=/path/to/python ./scripts/rerun_paper_experiments.sh --print-fingerprint
```

确认指纹后，在干净的实现提交上执行：

```bash
YIAN_PYTHON=/path/to/python ./scripts/rerun_paper_experiments.sh \
  --expect-fingerprint <fingerprint_sha256> --pin 4
```

脚本先运行全部正确性与 IR 审计，再运行 14×3 和 ASan 实验。所有
阶段成功后才更新 `docs/` 和 `paper/data/` 的冻结数据；失败或中断会恢复
tracked 实验数据。

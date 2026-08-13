# fat_cve 实验：胖指针安全机制拦截真实 CVE 漏洞

本实验验证 YIAN 胖指针安全机制（`docs/security.md` 形式化论证 + `docs/security-code.md`
实现设计）对**真实世界 CVE 漏洞**的拦截能力：把历史上真实发生的 C/C++ 内存安全漏洞
（越界、UAF、双释放、栈悬垂、释放带偏移、跨对象指针差/比较等）用 YIAN 语法逐一移植为
负例，确认每一个都被运行时 trap 拦截；再提供对应的修复版本（`-fixed.an`）确认修复后
程序正常通过。

## 目录结构

```
tests/fat_cve/
├── cve/
│   └── CVE-YYYY-NNNNN/          # 每个 CVE 一个子目录
│       ├── CVE-YYYY-NNNNN.an    # 负例：复刻漏洞形态，应被胖指针机制 trap
│       ├── CVE-YYYY-NNNNN-fixed.an  # 修复版：等价语义的正确实现，应通过
│       └── (可选 README.md / 备注)  # 漏洞说明与移植说明
├── docs/
│   ├── README.md                # 本文件
│   ├── SYNTAX.md                # 旧语法 → 胖指针语法迁移指南
│   └── MECHANISMS.md            # 12 机制索引：规则号 → 负例形态 → CVE 归属
└── (期望输出镜像在 tests/output/fat_cve/ 下)
```

期望输出（`.ans`）不在 CVE 目录内，而是镜像在 `tests/output/fat_cve/`：

```
tests/output/fat_cve/
└── cve/
    └── CVE-YYYY-NNNNN/
        └── CVE-YYYY-NNNNN.an.ans   # 负例的期望输出（Exit code -4 等）
```

负例 `.an` 有对应 `.ans`；`-fixed.an` 无 `.ans`（成功测试：退出码 0、无输出比对）。

## 运行方式

```bash
python3 scripts/run_fat_cve.py              # 运行全部 CVE 用例
python3 scripts/run_fat_cve.py -f CVE-2023-45664  # 只跑名字含该 CVE 的用例
python3 scripts/run_fat_cve.py -v           # 失败详情
python3 scripts/run_fat_cve.py -q           # 只输出汇总行
python3 scripts/run_fat_cve.py --no-run     # 只编译不执行
```

退出码：全部通过为 0，任一失败为 1。失败详情同时写入 `build/test_failures_fat_cve.log`。

主测试套件 `scripts/run_tests.py` 已排除 `tests/fat_cve/`（与 `tests/fat/` 并列），
两者互不干扰。

## 命名与用例约定

- 用例名 = 相对 `tests/fat_cve/` 的路径，如 `cve/CVE-2023-45664/CVE-2023-45664.an`。
- 每个 `<CVE-ID>/` 目录内所有 `.an` 均独立编译执行（含 `-fixed.an`）。
- 负例（复刻漏洞）期望在胖指针检查点 trap（`llvm.trap` → `SIGILL` → `Exit code -4`），
  其 `.ans` 以 `Exit code -4` 为首行（语义见 `scripts/run_tests.py::_parse_ans`）。
- 修复版（`-fixed.an`）为成功测试：无 `.ans`，期望退出码 0。

## 机制索引（简版）

完整索引见 `docs/MECHANISMS.md`。12 个机制概览：

| # | 机制 | 定义/规则出处 | 拦截的漏洞类别 |
|---|------|--------------|----------------|
| 1 | 5 字段表示 | security.md §2.4 定义 11 | 表示层基础（一切检查的载体） |
| 2 | Gen/键 | §2.3 定义 10 | 作废永久性：释放/帧退出后指针永不复活 |
| 3 | 锁槽 | §2.3 定义 7 | 时序检查载体：块头/帧首一个机器字 |
| 4 | in_bounds | §2.4 定义 12、规则 3.2.1-3.2.2/3.5.2 | 越界读/写、one-past-end 访问 |
| 5 | live | §2.3 定义 8、规则 3.2.1-3.2.2/3.6.2 | UAF、双释放、栈悬垂 |
| 6 | is_heap | §2.3 定义 9、规则 3.6.2 | 释放栈指针 |
| 7 | is_raw | §2.5、规则 3.6.2 | 释放带偏移/子对象指针 |
| 8 | 帧锁 re-key | §2.6、规则 3.7.1-3.7.2 | 栈悬垂（函数返回后访问局部地址） |
| 9 | 比较字段化 | §3.4、规则 3.4.1-3.4.2 | 跨对象序比较 trap |
| 10 | PtrDiff | §3.3、规则 3.3.3 | 跨对象指针差 trap |
| 11 | ZST | security-code.md §7.6 风险 2 | 指针-to-ZST 保持 ZST 快路径（无检查点，负例仅验证语义正确） |
| 12 | 受限操作 | security-code.md §8.3 | bitcast/from_raw_parts 等仅限标准库（编译期拒绝） |

## CVE 清单（30 个）

清单来自 `bak/old_exp/cwe/`（老实验参考源，每个 CVE 含 `.c`/`.an` 两文件）：

| # | CVE-ID | 漏洞类型 | 机制归属 | 状态 |
|---|--------|---------|---------|------|
| 1 | CVE-2020-36617 | 空指针解引用 (CWE-476) | live 定义 8 null 短路;规则 3.2.1 | 已通过 |
| 2 | CVE-2021-32495 | 释放后使用 (CWE-416) | live 定义 8;规则 3.2.1 | 已通过 |
| 3 | CVE-2021-32845 | 越界写 (CWE-787) | in_bounds 定义 12;规则 3.2.2 | 已通过 |
| 4 | CVE-2021-33304 | 双重释放兼 UAF 访问 (CWE-415/416) | live 定义 8;规则 3.2.1/3.6.2 | 已通过 |
| 5 | CVE-2021-37778 | 栈缓冲区溢出 (CWE-121) | in_bounds 定义 12;规则 3.2.2 | 已通过 |
| 6 | CVE-2021-39228 | 释放后使用 (CWE-416) | live 定义 8;规则 3.2.1 | 已通过 |
| 7 | CVE-2022-0523 | 释放后使用 (CWE-416) | live 定义 8;规则 3.2.1/3.6.2 | 已通过 |
| 8 | CVE-2022-31003 | 越界读 (CWE-125) | in_bounds 定义 12;规则 3.2.1/3.3.1 | 已通过 |
| 9 | CVE-2022-3806 | 释放后使用 / 双重释放 (CWE-416/415) | live 定义 8;规则 3.2.2/3.6.2 | 已通过 |
| 10 | CVE-2023-0341 | 栈缓冲区溢出越界写 (CWE-121/787) | in_bounds 定义 12;规则 3.2.2/3.3.1 | 已通过 |
| 11 | CVE-2023-1452 | 栈缓冲区溢出越界写 (CWE-121/787) | in_bounds 定义 12;规则 3.2.2 | 已通过 |
| 12 | CVE-2023-23086 | 越界读 (CWE-125),无闭引号扫描过界 | in_bounds 定义 12;规则 3.2.1 | 已通过 |
| 13 | CVE-2023-26463 | 悬垂返回指针 + 空指针解引用 (CWE-562/476) | 帧锁 re-key 定义 15;规则 3.7.1-3.7.2 | 已通过 |
| 14 | CVE-2023-2905 | 越界读 (CWE-125) | in_bounds 定义 12;规则 3.2.1 | 已通过 |
| 15 | CVE-2023-2977 | 越界读 (CWE-125),ASN.1 解析越界 | in_bounds 定义 12;规则 3.2.1 | 已通过 |
| 16 | CVE-2023-36273 | 越界读 (CWE-125),CRC 长度越界 | in_bounds 定义 12;规则 3.2.1 | 已通过 |
| 17 | CVE-2023-39351 | 空/零长度分配解引用 (CWE-476) | in_bounds 定义 12;规则 3.2.1 | 已通过 |
| 18 | CVE-2023-41361 | 栈缓冲区溢出,memcpy 无界拷贝 (CWE-787) | in_bounds 定义 12;规则 3.2.2 | 已通过 |
| 19 | CVE-2023-4264 | 栈/全局缓冲区溢出 (CWE-121) | in_bounds 定义 12;规则 3.2.2 | 已通过 |
| 20 | CVE-2023-45664 | 双重释放 (CWE-415),realloc 大小错误 | live 定义 8;delete 前提 3.6.2 | 已通过 |
| 21 | CVE-2023-49287 | 栈缓冲区溢出 (CWE-121) | in_bounds 定义 12;规则 3.2.2 | 已通过 |
| 22 | CVE-2023-49606 | 释放后使用 UAF 读 (CWE-416) | live 定义 8;规则 3.2.1 | 已通过 |
| 23 | CVE-2024-23310 | realloc 后旧指针悬垂 UAF 写 (CWE-416) | live 定义 8;规则 3.2.2 | 已通过 |
| 24 | CVE-2024-37407 | 负长度/零长度越界读 (CWE-125) | in_bounds 定义 12;规则 3.2.1 | 已通过 |
| 25 | CVE-2024-40493 | 空指针解引用 (CWE-476) | live null 短路 + in_bounds;规则 3.2.1 | 已通过 |
| 26 | CVE-2024-42478 | 越界读 (CWE-125),memcpy 超数据源 | in_bounds 定义 12;规则 3.2.1 | 已通过 |
| 27 | CVE-2024-45508 | 越界读/写,剥离纯空白节点 (CWE-125/787) | in_bounds 定义 12;规则 3.2.1 | 已通过 |
| 28 | CVE-2024-47538 | 栈缓冲区溢出写 (CWE-121/787),Vorbis position[] | in_bounds 定义 12;规则 3.2.2 | 已通过 |
| 29 | CVE-2024-47777 | 越界读 (CWE-125),短 smpl chunk 缓冲 | in_bounds 定义 12;规则 3.2.1 | 已通过 |
| 30 | CVE-2025-47917 | 释放后使用 UAF 读 (CWE-416) | live 定义 8;规则 3.2.1 | 已通过 |

状态列说明：`已通过` = 负例编译 0、运行 trap（SIGILL → Exit code -4），修复版编译 0、
运行 exit 0（`scripts/run_fat_cve.py` 全量 60/60 PASS）。全部 30 个 CVE 负例均被
对应机制拦截；具体触发路径见各 `cve/CVE-*/MAPPING.md`。

## 参考文档

- `docs/security.md` — 胖指针形式化论证（定义 1-23、规则 3.2.1-3.7.2、S1/T1/L-KEY/L-NOKEY）
- `docs/security-code.md` — 编译器实现设计（CFG 层引入 §7、类型系统约束 §8）
- `scripts/run_fat_tests.py` — 本脚本的直接模板（tests/fat/ 独立 runner）
- `scripts/run_tests.py` — 主 runner（发现/解析/执行核心，本脚本 import 复用）

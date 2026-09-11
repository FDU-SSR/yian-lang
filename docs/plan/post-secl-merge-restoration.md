# SECL 合入后功能恢复清单

本文记录 SECL 合入 develop 前，为了让两边的公共代码和测试边界保持一致而暂时回退的 develop 单边功能。它不是本次合入的实现计划；合入完成后，应按依赖顺序重新评估并逐项恢复。

## 记录范围

- 本清单对应 `sync/develop-pre-secl` 阶段 4 的实际回退结果。
- 回退只针对 develop 已有、SECL 当前没有等价实现的功能块；胖指针、引用类型、非空指针、niche、`comptime if` 和普通浅复制语义不在本清单中。
- 没有为了维持旧测试数量而保留只验证已回退功能的测试，也没有新增测试或修改现有断言。
- `main` 的普通入口和 C ABI 包装仍属于两边共同的程序入口机制，未随进程参数接口回退。

## 已暂时回退的功能

| 功能 | 原始来源 | 阶段 4 处理 | 合入后恢复前提 |
| --- | --- | --- | --- |
| `std.core.env` 与进程参数/退出接口 | `da0ccac`、`97da92d`；后续整理为 `572657f` | 删除 `lib/core/env.an`、`__yian_argc`、`__yian_argv_ptr`、`__yian_cstrlen`、`__yian_exit` 及其 CallDispatcher、HIR、CFG、LLVM、受限操作链路；删除只测试 args/exit 的既有测试和期望文件 | 先确定胖指针与外部 C ABI 的 argv 边界，再恢复完整接口；应重新设计失败返回和非空指针表示，不能直接恢复当前裸指针 ABI |
| `From<str> for f64` | `3c01430`；签名后续由 `cfa38f7` 调整 | 仅删除 `lib/num/f64.an` 中的字符串解析转换和对应 `tests/std/num/f64.an`；保留 f64 的 Hash 及其他共同接口 | 确定公共字符串视图/字节访问接口后，重新移植解析实现并恢复独立测试 |
| RawVec 容量上限和释放后 panic | `12066ba`、`85ccd67` | 删除 develop 单边的容量最高位边界 panic、释放后 `ptr/grow/reserve/shrink_to_fit` panic，以及仅验证容量上限的 `raw_vec_limit` 测试；保留 SECL 的 live-bit、非空分配、元素搬运和显式释放路径 | 统一两边的容器释放状态和错误处理语义后，再恢复可证明兼容的容量限制；不能直接恢复依赖另一种状态表示的检查 |
| HashMap 释放后保护 | `12066ba`、`85ccd67` | 删除 `insert`/`clone` 对 `capacity == 0` 的 develop 单边 panic；保留容量状态检查、有效槽位清理、浅复制和 SECL 释放状态；删除仅测试释放后 clone 的既有测试 | 先统一 HashMap 的释放后状态、浅别名和重复释放语义，再决定恢复 API 级拒绝或改用统一错误路径 |

### 进程接口的具体删除边界

以下代码块已经从 develop 准备分支删除：

- `compiler/analysis/lowering/call_dispatcher.py` 中四个 YIAN 进程内建的名称注册和 lowering；
- `compiler/analysis/unit/hir.py`、`hir_export.py` 中对应的 HIR 节点；
- `compiler/codegen/cfg/ir.py`、`builder.py`、`dump.py` 中对应的 CFG 节点、终结器和解析路径；
- `compiler/codegen/llvm/builder.py`、`intrinsics.py`、`translator.py` 中的参数读取、C 字符串长度和退出 lowering；
- `compiler/codegen/llvm/module.py` 中保存 argc/argv 的内部全局及包装器中的存储动作；包装器本身仍保留，只负责调用 `__yian_main` 并返回成功码；
- `compiler/analysis/passes/restricted_ops.py` 中四个已删除进程内建的受限名称；
- `compiler/analysis/passes/type_check.py` 中对 `std.core.env.exit` 的引导性错误文本；
- `lib/core/env.an` 及 `tests/env` 下只覆盖命令行参数和退出码的现有测试材料。

与标准输入相关的 `tests/env/stdin.an`、`stdin_all.an` 及其输入文件没有删除，因为它们验证的是共同的 `sys_read`/IO 路径，不依赖 `std.core.env` 的参数或退出接口。

## 尚未回退、需要后续审计的差异

### 泛型参数推断中的数组退化

当前保留 `compiler/analysis/lowering/call_dispatcher.py` 的 `__decayed_type_for_inference`。它不是本阶段可以安全删除的独立装饰：develop 的普通数组退化测试包含泛型调用 `read_first(arr)`，删除该辅助逻辑会使 `T[N] -> T*` 在泛型参数推断阶段失败，即使表达式层仍有普通数组退化 coercion。当前 SECL 没有确认等价的推导路径。

因此本项没有被伪回退，而是转交阶段 5/6 做共同语义审计：

1. 先确认 SECL 是否已有未被识别的等价推导路径；
2. 若没有，则将该 helper 作为两边共同数组退化语义移植到 SECL；
3. 只有在建立等价路径并完成现有回归后，才可删除重复实现或收敛代码。

普通数组到指针的表达式 coercion 本身也没有删除。

## 阶段 2、3、7 的合并后工作项

以下事项来自后续阶段的实际验证结果。它们只登记当前覆盖缺口或两种表示/失败路径的差异，不表示本次合并前已经解决；本阶段不通过修改测试预期来消除这些差异。

### T& 的专项覆盖缺口

阶段 2 没有从 SECL 找到可以直接迁移、且不依赖胖指针检查的独立测试，因此以下 T& 路径只完成了代码对照和已有回归验证，合并后允许补充专项覆盖：

- 泛型参数和返回值中的 `T&`；
- `typedef` 或其他类型别名中的 `T&`；
- 显式闭包接收者中的 `T&`；
- ZST 引用的布局、调用和删除边界。

### `comptime if` 的覆盖缺口

阶段 3 只实现了当前需要的 HIR 专门化子集，并遵守“不新增测试”的约束。合并后需要补充：

- 嵌套 `comptime if`；
- 更多可编译期求值的常量表达式组合；
- HIR 分支裁剪、未选 DefPoint 清理和错误诊断的正式回归覆盖。

### 阶段 7 接受的失败

统一 runner 后，以下 3 个安全失败路径在两边的退出表现不同：

| 测试 | develop 预期 | SECL 当前表现 | 合并后处理 |
| --- | --- | --- | --- |
| `tests/array/safety_oob.an` | 退出码 1，并输出既有诊断 | SIGILL，退出码 `-4` | 统一安全失败处理后恢复 |
| `tests/array/safety_oob_write.an` | 退出码 1，并输出既有诊断 | SIGILL，退出码 `-4` | 统一安全失败处理后恢复 |
| `tests/dyn/malloc_overflow.an` | 退出码 1，并输出既有诊断 | SIGILL，退出码 `-4` | 统一安全失败处理后恢复 |

以下 7 个用例依赖薄/胖表示的尺寸或构造预期，暂不在合并前改变：

- `tests/enum/niche_custom.an`；
- `tests/enum/niche_negative.an`；
- `tests/enum/niche_option.an`；
- `tests/enum/niche_slice.an`；
- `tests/type/sizeof_full.an`；
- `tests/type/type_ctor.an`；
- `tests/type/type_ctor2.an`。

上述 10 个失败已经接受为合并后工作。它们不能通过修改源码、断言或 `.ans` 文件伪装为当前通过；统一表示、退出码和兼容测试策略后再恢复通过状态。

## 合入后的恢复顺序

建议按以下顺序恢复，避免恢复一个功能时再次引入尚未统一的表示差异：

1. **先确定外部 ABI 边界**：明确胖指针与 C `argc/argv` 的转换、失败处理、非空保证和 `main` 包装器接口，再恢复 `std.core.env` 及其受限内建。
2. **统一字符串视图和可信字节访问**：在公共字符串表示稳定后恢复 `From<str> for f64`，保留解析错误处理并重新验证 Hash 边界。
3. **统一 RawVec 状态语义**：先收敛 live-bit、零长度分配、重复释放和容量溢出处理，再决定是否恢复容量上限与释放后 API 拒绝。
4. **统一 HashMap 资源状态**：在 RawVec 和元素清理规则稳定后恢复释放后 `insert/clone` 的行为，确保浅副本不会造成错误的重复清理。
5. **收敛数组退化推断**：比较两边 generic inference 与表达式 coercion 的完整链路，保留普通数组退化行为，再消除实现差异。

每一步恢复都应复用阶段 4 记录的原始提交和当前 SECL/合并后代码，不整体恢复原提交。恢复工作属于合入后的开发任务，本轮不补充测试。

## 阶段 4 验证记录

阶段 4 在 `sync/develop-pre-secl` 完成了两个代码提交：

- `86f85e1 chore: defer develop-only process interfaces`
- `9058e72 chore: defer remaining develop-only features`

结果：

- 正常测试：`284/287`，3 个失败仍为阶段 2 已登记的 `fmt/fstring_basic.an`、`fmt/fstring_nested.an`、`std/core/ops/str_concat.an`；
- experimental：`284/337`，保留原有 50 个失败并加上上述 3 个延期失败；
- anx：`19/19`；
- `python3 -m compileall -q compiler anx`：通过；
- `git diff --check`：通过；
- pyright：仍只有既有 `compiler/codegen/cfg/dump.py`、`compiler/codegen/llvm/module.py` 和 `compiler/utils/log.py` 诊断，未因本阶段产生新的类别；
- `compiler`、`lib`、有效测试源码中不再存在四个已删除 YIAN 进程内建或 `std.core.env` 引用。

该文件应在 `sync/develop-pre-secl` 和 `sync/secl-pre-merge` 保持逐字一致。它只描述待恢复内容，不代表这些功能已经在 SECL 中实现。

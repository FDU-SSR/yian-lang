# 堆分配强制初始化实施计划

本计划落地"用户代码无法取得未初始化堆内存"的语言改动：分配即带初值，裸分配收敛到受信
`@alloc<T>(n)`。目标是构造性关闭"字节 → 胖指针"的伪造通道（对应
`bak/security-poc/` 的 01–07）。安全语义的纵深项（key 熵、空间归属、记录认证）不在本计划内。

## 1. 目标与范围

### 1.1 目标

1. 用户堆分配只有两种形态，二者都交付已初始化内存：
   - `dyn value`（单元素，现有）；
   - `dyn[size] value`（同值初始化，新增）。
2. 删除裸形式 `dyn[size] Type`；未初始化分配统一由受信 builtin `@alloc<T>(n)` 承担，
   非受信来源不可书写。
3. 标准库边界纪律：跨边界指针/切片的 `size` 收敛到已初始化前缀，使未初始化容量不可寻址。
4. 迁移标准库与测试，使"分配后填充"的写法显式提供初值，或改用 `Vec`。

### 1.2 不在本计划内

- 不引入零值/默认值设施（**已决**：不新增 `@zero<T>()`，不引入 `Default` trait）；
- 不引入 `MaybeUninit<T>` 之类未初始化类型（**已决**）；
- key 随机化、空间归属校验、生命周期记录认证（纵深，另立条目）；
- `RawVec` 等内部类型的可见性控制本身（见 `bak/todo.md` #5）；本计划只做过渡期的
  `size` 收敛，不替代可见性控制；
- raw 指针模式语义、16 B 块头与胖指针布局 ABI、错误码集合：均不变。

### 1.3 已决语义（本次冻结）

**`dyn[size] value` 的语义**：初始值表达式**求值一次**，再把该值按位复制到全部 `size`
个元素；无 `T: Clone` 约束。

- "同值初始化"即由此定义；这与 `[v; n]`（`HIR.ArrayRepeat`）的复制语义一致，但**不复用其
  节点与降级**：`ArrayRepeat` 的 CFG 降级只支持编译期常量 count，并经
  `__build_array_construct` 物化栈数组（见 §2）；`dyn[size] value` 需要独立降级，直接写入
  堆块并支持动态 `size`。
- 已知性质（需在语法文档写明）：对拥有资源的类型（`Vec`、`String` 等）会产生 `size` 份浅
  拷贝别名，这与 `[v; n]` 同源，不是本次新引入；构造表达式不会被求值 `size` 次。
- 需要 `size` 个**互相独立**构造的元素时，使用 `Vec` 逐个 push/构造。

**零值/默认值不可用**的直接后果：`dyn[size] value` 要求元素类型存在一个用户可书写的值。
指针、切片、结构体等无法凭空造出占位值的元素类型，不再能用 `dyn` 建立"预留后填充"的堆数组；
这类需求改走 `Vec`（其容量路径由标准库经 `@alloc` 承担）。依赖未初始化堆数组的测试按 §5.8
删除或改写。

**`@alloc<T>(n)` 的形态**：受信 builtin，返回未初始化的 `T*`（`size = n`）。返回类型就是普通
指针类型，不引入未初始化类型；安全性由标准库纪律 + §5.7 的边界收敛保证，并以 §6 的可选 lint
保持可审计。

## 2. 现状与落点

| 层 | 位置 | 现状 |
| --- | --- | --- |
| AST | `frontend/parse/ast.py:625` `DynValue`、`:634` `DynBuffer(target_type, size)` | `DynBuffer` 无初值 |
| Parser | `frontend/parse/parser_expr.py:66` `__parse_dyn` | `]` 后 `parse_type()` → `DynBuffer`；否则 `DynValue` |
| HIR | `analysis/unit/hir.py:312` `DynValue`、`:320` `DynBuffer(element_type, length)` | 同上 |
| 下降 | `analysis/lowering/op_builder.py:208` `build_dyn_value`、`:214` `build_dyn_buffer`；`expr_checker.py:156/159` | `build_dyn_buffer` 只接受类型 |
| CFG | `codegen/cfg/builder.py:968` `__resolve_dyn_value`、`:979` `__resolve_dyn_buffer` | 前者已是"malloc + store"，后者只 malloc |
| CFG 控制流 | `codegen/cfg/builder.py:28` `LoopCtx`、`:404` `__translate_loop`、`__new_block`/`__switch_to`/`__set_terminator` | **结构化 builder，可在表达式降级中发循环** |
| CFG 分配 | `codegen/cfg/builder.py:1514` `__build_malloc` → `IR.Malloc`（`cfg/ir.py:151`）→ LLVM `builder.py:443` `malloc` | 元数据（key/size）在编译器侧构造 |
| 受限原语 | `analysis/passes/restricted_ops.py:19` `RESTRICTED_BUILTINS`，`check()` 按 `unit.allows_restricted_ops` 跳过受信单元 | 现成信任边界 |
| builtin 解析 | `frontend/parse/parser_expr.py:402` `__parse_builtin`、`:427` `__parse_bitcast` | `<Type>(...)` 类型实参范式现成 |
| ArrayRepeat（**不复用**） | 语义 `analysis/lowering/expr_checker.py:322`；CFG `codegen/cfg/builder.py:1061` | 只支持编译期常量 count，经 `__build_array_construct` 物化栈数组 |
| 标准库容量路径 | `lib/src/core/raw_vec.an:12/28/58/99/123/130`、`lib/src/collections/hash_map.an:36/40/186/190`、`lib/src/core/mem.an:27` | 共 11 处 `dyn[` |
| 暴露点 | `lib/src/core/raw_vec.an:35` `pub fn ptr() -> T*` | 返回 `size = capacity`，仅前 `len` 个已初始化 |
| 正确对照 | `lib/src/core/vec.an:206` `Vec::index` | 先查 `len` 再 `ptr() + index`，已是正确写法 |
| 迁移量 | `lib/` 11 处、`tests/` 310 处 `dyn[` | 见 §5.8 |

## 3. 设计定案

| 形态 | 语义 | 可见性 |
| --- | --- | --- |
| `dyn value` | 分配 1 个 `T` 并写入 `value`（现有行为） | 用户 + 标准库 |
| `dyn[size] value` | 分配 `size` 个 `T`，全部写入 `value`（求值一次 + 位复制，支持动态 `size`） | 用户 + 标准库 |
| `@alloc<T>(n)` | 分配 `n` 个未初始化 `T`，返回 `T*`（`size = n`） | **仅受信来源** |

职责划分：不接受"初值 + 覆盖"的双写、或需要元素独立构造/增长/惰性构造的场景，用户改用 `Vec`。

## 4. 分期

| 期 | 内容 | 退出条件 |
| --- | --- | --- |
| P1 | AST/HIR/parser/export/`@alloc` 骨架（此时**不**改变既有 `dyn[n] T` 行为） | 新增节点全链路可编译；`@alloc` 仅受信可用；三套件不变 |
| P2 | `dyn[size] value` 落地；**删除裸 `dyn[size] Type`** | 用户源码裸形式报错；`dyn[n] value`（含动态 `n`）语义测试通过 |
| P3 | 标准库迁移 + 边界 `size` 收敛 | lib 不再使用裸形式；边界 API 全部按已初始化前缀约束 |
| P4 | 测试迁移（删除/改写）+ 负向用例 + 验收判定 | 三套件两模式全绿；`bak/security-poc` 语义验收通过 |
| P5（可选） | `@uninit` 审计 lint；分配器零页优化；key 熵 | 按需 |

## 5. 逐文件改动清单

### 5.1 AST（`compiler/frontend/parse/ast.py`）

- `BuiltinKind` 增 `Alloc = "alloc"`（不增 `Zero`）。
- `DynBuffer` 字段改为 `{size, element}`（`element: Expr`），删除 `target_type`。
- 新增 `AST.Alloc(target_type, count)`。
- 联合类型别名（`:762`）同步。

### 5.2 Parser（`compiler/frontend/parse/parser_expr.py`）

- `__parse_dyn`（`:66`）：`]` 后调用 `parse_expr()` → `DynBuffer(size, element)`；若得到类型表达式
  （`AST.TypeItem` 等）给定向诊断"未初始化分配请使用 `@alloc<T>(n)`"。
- 新增 `__parse_alloc`，镜像 `__parse_bitcast`（`:427`）：`<Type>` + `(count)`。
- `__parse_builtin`（`:402`）分派 `Alloc`。

### 5.3 AST 访问者（必须全部同步，漏一处即崩溃或静默错误）

- `analysis/index.py:653/655`
- `analysis/passes/desugar.py:364/366`、`:431/433`
- `analysis/passes/restricted_ops.py:97/99`；`RESTRICTED_BUILTINS` 增 `Alloc`；
  `__scan_expr` 需遍历 `Alloc.count`、`DynBuffer.element`
- `frontend/parse/ast_export.py:335/337`、`:463/469`

### 5.4 HIR 与分析

- `analysis/unit/hir.py`：`DynBuffer` → `{size, element, element_type}`；新增 `HIR.Alloc`；
  联合别名（`:542`）同步。
- `analysis/unit/hir_export.py:165/167`、`:384/390`。
- `analysis/lowering/op_builder.py:208/214`：`build_dyn_buffer` 接收初值表达式（元素类型取自
  它）；新增 `build_alloc`。
- `analysis/lowering/expr_checker.py:90/92`、`:156/159`、`:504`。
- `analysis/lowering/call_dispatcher.py`：`Alloc` 分派（返回 `T*`）。
- `analysis/passes/comptime_if.py:129/131`、`analysis/passes/closure_lowering.py:210/212`、
  `:397/399`。
- `analysis/passes/definite_assignment.py:341/344`、`:695/698`：`DynBuffer` 需遍历 `element`；
  新增 `Alloc`（遍历 `count`）。

### 5.5 CFG / LLVM

- `codegen/cfg/builder.py:630/632` 分派；新增 `__resolve_alloc`（仅 `__build_malloc`）。
- `__resolve_dyn_buffer`（`:979`）独立降级为"分配 + 循环写入"，**不复用** `ArrayRepeat`：
  1. `count = __resolve_val(expr.length)`；`buf = __build_malloc(element_type, count)`；
  2. `value = __resolve_val(expr.element)` 求值一次（位于支配全部循环块的块内）；
  3. 用 `LoopCtx` 同款机制建 header/body/exit：`i < count` 为真则
     `__build_element_ptr(buf, i)` 后 `__build_store(value, elem_ptr)`，`i += 1`，回 header；
  4. exit 返回 `buf`。
  - 常量 `count` 可展开为直线存储；动态 `count` 走上面的循环，无需新 CFG IR 节点，也无需
    运行时辅助函数。
  - 循环需正确处理 `__current_block` 终止符与 `__loops` 栈；元素表达式本身含控制流/调用时，
    沿用现有 `__resolve_val` 的块构造路径。
- 不使用运行时 `__yian_fill` 之类的字节复制辅助（语义是"同值复制"，但经 CFG 循环直接 store
  更简单，且避免了"取初值地址"的问题）。若后续需要按字节 memset 的快速路径，可作为纯优化加回。
- LLVM 语句集与 `builder.py:443` `malloc` 均不改。

### 5.6 运行时

本计划无必需的运行时改动。可选优化（P5）：`__secl_pool_alloc_zeroed`（仅对空闲链复用块清零；
新 mmap 页本就为零）。

### 5.7 标准库迁移（11 处）

- `lib/src/core/raw_vec.an:12/28/58/99/123/130`
- `lib/src/collections/hash_map.an:36/40/186/190`
- `lib/src/core/mem.an:27`

全部改为 `@alloc<T>(n)`（受信路径，不付初始化成本，`with_capacity`/`grow` 性能不变）。

边界收敛（实测修正，按"建议"取审计范围）：

- **原方案"把 `raw_vec.an:35` `ptr()` 收敛到 `size = len`"不可行**：`Vec::push`
  （`vec.an:45`）正是用同一个指针在 `index = len < capacity` 处写入尚未初始化的槽；
  把 `size` 收敛到 `len` 会让 `*end` 的 `in_bounds` 失败，破坏 push。
- 实测的用户可达路径只有一个：**用户直接 `from std.core.raw_vec import RawVec`**，
  `with_capacity(n)` 后 `ptr()[i] (i < n)` 读取未初始化载荷被安全检查放行
  （PoC：`bak/security-poc/09_rawvec_uninit_read.an`，读取被接受，程序走到后续断言语句）。
- `Vec` 的公开面**已经**是 len 收敛的：`Vec::index`（`vec.an:206`）先查 `len`，
  `Vec::as_slice`（`vec.an:34`）用 `@slice_from_parts(ptr, len)`；`Slice::ptr()` 由切片长度约束。
- 因此这一项**只能靠可见性控制解决**（`bak/todo.md` #5），或在可见性机制落地前保留为
  已记录的信任边界欠账；本计划不引入 `MaybeUninit`（已决），也不为零初始化泛型 `T` 发明默认值。
- 审计结论（公开面已 bounded）：`core/vec.an`、`core/slice.an`、`core/mem.an`、`core/str*`、
  `collections/hash_map.an` 未见 capacity 尺寸指针外泄；唯一缺口是 `core/raw_vec.an` 自身可被导入。

### 5.8 测试迁移（310 处）

- "分配后填充"型，元素类型有可写值 → `dyn[n] <初值>`（数值用 `0` 等普通字面量），接受双写；
- 元素类型无法给出初值（指针/无默认结构体的"预留后填充"）→ **删除该用例或改写为 `Vec`**；
- "需要增长/惰性/逐元素独立构造"型 → 改 `Vec`；
- 新增负向用例（参照 `tests/basic/error/restricted_*.err.an` 范式）：
  - 用户源码裸 `dyn[n] T` 报错；
  - 非受信源码 `@alloc<T>(n)` 报错；
- 新增正向用例：`dyn[n] value` 的常量与动态 `n`、同值复制语义、按位复制对拥有类型的行为。
- 迁移前先统计"填充型/无初值型"占比，量化删除与改写范围。

## 6. 验证与门

- `python3 -m compileall -q compiler anx scripts`；pyright strict（`compiler/`、`anx/`）。
- `python3 scripts/run_tests.py --all`（basic + safety + package，fat 与 raw 两模式）全绿。
- `python3 runtime/build.py --check`（并跑 `--asan`）。
- **验收判定（关键）**：把 `bak/security-poc/` 的 01–07 按新语义重跑，期望：
  - 01 / 06 / 07：无法编译（裸 `dyn[n] T` 已移除、无字节→指针通道）；
  - 02 / 03 / 05：改写后若仍能构造，必须在 `RawVec::ptr` 收敛后以 S001 失败；
  - 08：仍 S002。
- 可选（P5）：`@uninit` 属性 + lint，强制"未标记函数不得传播可未初始化的 `T*`"，让标准库
  边界 TCB 可 grep。
- `git diff --check`。

## 7. 风险与回退

- **表达力**：没有零值设施后，"堆上预留后填充"的用户写法只能经 `Vec`；依赖它的测试需删除或
  改写（§5.8 已计入工作量）。
- **迁移**：`lib/` 11 处 + `tests/` 310 处；机械但量大，且含删除项。
- **性能**：`dyn[n] v` 之后全体覆盖造成双写；缓解见 §5.6 的零页优化与常量 `count` 展开。
- **浅复制别名**：与 `[v; n]` 同源，非本次新增，按 §1.3 写入语法文档。
- **回退策略**：P1/P2 可先 warning 后 error；`@alloc` 与旧 `dyn[n] T` 双轨期使 lib 与 tests
  可分阶段迁移，任一期可独立回滚。

## 8. 决策与遗留

已决：

1. 不引入零值/默认值设施；相关测试删除或改写。
2. `dyn[n] value` 不复用 `ArrayRepeat`，不加 `T: Clone`；语义为求值一次 + 位复制。
3. 不引入 `MaybeUninit`；`@alloc` 直接返回 `T*`，安全由标准库纪律 + 边界收敛保证。
4. 边界审计范围按 §5.7 执行（`RawVec`/`Vec`/`slice`/`mem`/`str`/`HashMap`）。

遗留（实现期确认，不影响开工）：

- 动态 `count` 的循环是否需要按 `count` 与元素大小做展开/分档优化（P5）。
- `@uninit` lint 是否纳入 P5 或更晚。
- 删除型测试的清单与替代覆盖（哪些能力改用 `Vec` 后仍需保留回归）。

## 9. 实施状态（P1–P4 已完成）

| 期 | 状态 | 落地内容 |
| --- | --- | --- |
| P1 | ✅ | `@alloc<T>(n)` 端到端（`ast.py`/`parser_expr.py`/`op_builder.py`/`expr_checker.py`/`hir.py`/CFG `__resolve_alloc`/`restricted_ops`/全部 AST+HIR 访问者/export）；用户源码书写报 E501；标准库 `raw_vec.an` 首处迁移验证。 |
| P2 | ✅ | `dyn[size] value` 落地：`]` 后统一解析表达式，类型名在下降期识别为非 ZST 即报 E499；ZST 元素保留裸形式（只有一个值，恒已初始化）；CFG 独立降级用 `LoopCtx` 同款块结构发"求值一次 + 元素写入"循环，支持动态 `size`，未引入运行时辅助。 |
| P3 | ✅ | 标准库 11 处 `dyn[...] T` → `@alloc<T>(n)`；边界审计见 §5.7 的实测修正（`Vec`/`Slice` 公开面已 bounded；唯一缺口是 `RawVec` 可见性，归 `bak/todo.md` #5）。 |
| P4 | ✅ | 数值型 259 处脚本化迁移 + 结构体/指针/ZST 共 42 处逐个人工处理；删除 `trait/vector.an`（"先分配后填充"的用户自造 Vec，设计上改走 std `Vec`）与前提被推翻的 `error/dyn_variable.err.an`；新增 `dyn/repeat.an`、`error/restricted_alloc.err.an`、`error/dyn_uninitialized.err.an`；三套件两模式全绿（basic 754 / safety 156 / package 99）。 |

验收：`bak/security-poc/01–08` 全部编译期拒绝；把 02 改写成新语法后复用链失效；
`09` 仍可达并已记录为 `RawVec` 可见性欠账。

实现期发现的两点已回填：§5.7 的 `ptr()` size 收敛不可行（破坏 `Vec::push`）；
`dyn[n] T*` 旧写法的诊断已由 E203 改善为定向 E299（`'dyn[n]' initializes every element
from a value, not a type`），回归夹具 `tests/basic/error/dyn_pointer_type.err.an`。
解析器为此在 `TokenStream` 上增加了 `mark`/`reset` 用于失败后的回溯探测；仅在表达式解析
失败时才判定为"已移除的类型形式"，因此 `dyn[n] v`（变量初值）与 `dyn[n] v * 2`（乘法初值）
不受影响。


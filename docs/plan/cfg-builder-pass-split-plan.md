# CFG 构建拆分为多 pass 的设计（草案）

## 1. 目标与非目标

**目标**

1. 把 `compiler/codegen/cfg/builder.py` 从"下降 + 检查插入 + 检查优化 + CFG 后处理"的单一
   1770 行大类，拆成**一个只负责 HIR→CFG 下降的构建器**加**一条 CFG pass 管线**；
2. 让"检查插入"与"检查优化"成为可独立测试、可独立演进的 pass——它们才是接下来要动的地方
   （循环不变检查提升、检查融合；见 `fat-pointer-representation-plan.md` §3.7 的三项已知回退）；
3. 迁移期间**行为等价**：同一份源在 fat/raw 两模式下产出的 CFG IR（`--dump` 的 `cfg.txt`）
   与 LLVM IR（`-t ll`）逐字节可比。

**非目标**

- 不改语言语义、不改检查集合、不改错误码；
- 不顺手改 IR 节点定义（目前不需要；见 §5.3）；
- 不做"完整数据流框架"——只在需要的几条链上做保守分析。

## 2. 现状量化（2026-09-21 实测）

`compiler/codegen/cfg/builder.py`：**1770 行 / 一个类 `CfgBuilder` / ~130 方法**。

| 关注点 | 粗占比 | 代表方法 |
| --- | ---: | --- |
| HIR→CFG 下降 | ~1050 行 | `__translate_*`、`__resolve_*`、`__build_*` |
| 检查机制（插入/去重/合并/失效/出处） | ~290 行 | `__build_load`、`__build_store`、`__build_field_ptr`、`__build_element_ptr`、`__dedup`、`__merge_access`、`__invalidate_checks`、`__ptr_key`/`__pair_key`/`__value_key` |
| CFG 后处理 | ~190 行 | `__eliminate_dead_code`、`__sort_blocks_rpo`、`__guard_termination` |
| 发射管道 | ~200 行 | `__emit`、`__new_block`、`__switch_to`、`__set_terminator`、`__emit_phi`、`__new_name` |

检查节点共 **11 种**（`ir.py`）：`CheckSafeAccess`、`CheckRefAccess`、`CheckInBounds`、
`CheckElementArith`、`CheckElementAccess`、`CheckSliceNonEmpty`、`CheckRawBounds`、`CheckViewAccess`、
`CheckPtrDiff`、`CheckPtrCmp`、`CheckDelete`；**发射点 23 处**，散落在下降代码里：

| 发射点 | 节点 |
| --- | --- |
| `__build_load` / `__build_store` | `CheckSafeAccess(ptr, live=)` / `CheckRefAccess` |
| `__build_field_ptr` | `CheckRefAccess`、`CheckInBounds`（挂起义务）、`CheckElementAccess`（合并） |
| `__build_element_ptr` | `CheckElementArith`、`CheckInBounds`（挂起义务） |
| `__build_ptr_diff` / `__build_ptr_cmp` | `CheckPtrDiff` / `CheckPtrCmp` |
| `__translate_delete` | `CheckDelete`（随后失效检查状态） |
| `__resolve_array_access`（raw） | `CheckRawBounds` |
| cast 分支（数组退化 / `T[]→T&`） | `CheckInBounds`、`CheckSliceNonEmpty` |
| `__build_sys_read` / `__build_sys_write` | `CheckViewAccess` |
| 方法调用 receiver | `CheckInBounds` |

"要不要插"依赖**只在下降期存在的隐式状态**：

| 状态 | 含义 | 谁写 |
| --- | --- | --- |
| `__raw_ptrs` | 裸指针寄存器名（跳检查） | `VarPtr(raw)`、`Alloca(fat=False)`、`Cast(raw)`、派生 |
| `__frame_locked` | 锁字段取自本函数帧锁槽（live 恒真） | `__emit_frame_lock` / `__inherit_frame_lock` |
| `__live_known` | 刚分配、尚未释放（live 恒真） | `__build_malloc` 之后由填充循环登记 |
| `__fat_root` | 锁字段出处（去重键的基础） | `__build_var_ptr_*` / `__build_malloc` / `__inherit_root` |
| `__checked`、`__elem_derived`、`__field_derived` | 块内已发检查 / 派生链 / 挂起 InBounds 义务 | `__dedup`、`__merge_access`、`__invalidate_checks` |

## 3. 目标架构

```text
compiler/codegen/cfg/
  builder.py          # P0：HIR → CFG IR（只做数据流，不发检查）
  passes/
    __init__.py       # run_pipeline(func, ctx)：按序跑 pass，返回同一 IR.Function
    context.py        # PassContext：type_ctx / symbol_ctx / raw_pointers / 帧字 / 诊断通道
    provenance.py     # 分析：指针出处格 + root 等价类 + kill 规则（P1/P2 共用）
    insert_checks.py  # P1：按 §4 的规则表插入 Check*
    optimize_checks.py# P2：去重 / 合并 / 挂起义务 / （后续）循环不变检查提升
    cleanup.py        # P3：DCE / RPO 排序 / 终结保护
  ir.py               # 不变（§5.3）
  dump.py             # 不变（P3 之后仍需能 dump）
  lockmech.py         # 不变
```

**管线顺序与不变量**

| 步 | pass | 输入 → 输出 | 不变量 |
| --- | --- | --- | --- |
| P0 | `CfgBuilder.build()` | HIR → CFG IR | 每个 `Load`/`Store`/`Delete`… 前**没有** `Check*`；provenance 由节点自身携带（`VarPtr(raw)`、`Alloca(fat=)`、`Malloc`、`Cast(raw=)`、`FieldPtr`/`ElementPtr` 的派生边） |
| P1 | `insert_checks` | CFG IR → CFG IR | 每个需要的访问点前有对应 `Check*`；**不删**已有节点；只追加节点、不动 CFG 结构（§5.4） |
| P2 | `optimize_checks` | CFG IR → CFG IR | 只做"证明冗余后删除/合并"；删除的检查必须由同块支配它的等价检查覆盖；挂起 `CheckInBounds` 义务在失效点前补发 |
| P3 | `cleanup` | CFG IR → CFG IR | 块集合/顺序与今天的 `build()` 末尾一致 |

`PassContext`（`context.py`）只放**跨 pass 的只读事实**与诊断，不放可变检查状态：

```python
@dataclass
class PassContext:
    type_ctx: TypeCtx
    symbol_ctx: SymbolCtx
    raw_pointers: bool          # 诊断模式：全部 pass 直接跳过
    frame_word: IR.Value | None # 本函数 AcquireFrameLock 的结果（帧内判定用）
    func_name: str              # 诊断/块命名
```

### 3.1 拆分原则：内聚簇 + 独占字段一起搬（builder 变编排者）

判据三条，同时满足就整体搬走（方法 + **它独占的字段** + 只被它用的小 helper），
`CfgBuilder` 只留编排面——与 `analysis/lowering/` 把表达式/调用/算子下降外包给
`ExprChecker`/`CallDispatcher`/`OpBuilder` 是同一形态：

1. 簇内方法彼此调用远多于调用簇外；
2. 它有"只有它自己写"的私有字段；
3. 对外只需要一两个入口。

实测字段独占关系（`self.__*` 出现次数）：

| 字段 | 用量 | 归属 |
| --- | ---: | --- |
| `type_ctx` / `func` / `raw_pointers` / `counter` / `dp` / `func_name` / `symbol_ctx` | 57 / 24 / 11 / 5 / 2 / 2 / 2 | **共享**：进 `context.py` 的句柄（只读 + 发射原语） |
| `field_derived` / `elem_derived` / `fat_root` / `raw_ptrs` / `checked` / `frame_locked` / `live_known` | 15 / 9 / 8 / 7 / 6 / 5 / 4 | **检查簇独占** → `checks/`（§3.2 C3） |
| `defer_scopes` / `loops` | 9 / 5 | **语句/控制流簇独占** → `lower/stmts.py`（C4） |
| `frame_lock` | 5 | 惰值下降与检查共用 → 收进句柄的只读访问器（不搬） |

### 3.2 内聚簇与目标归属

| 簇 | 代表方法 | 独占字段 | 目标模块 | 依赖顺序 | 风险 |
| --- | --- | --- | --- | --- | --- |
| C1 CFG 后处理 | `__eliminate_dead_code`、`__sort_blocks_rpo`、`__guard_termination` | — | `passes/cleanup.py` | 1（叶子） | 低 |
| C2 发射句柄 | `__emit`、`__new_block`、`__switch_to`、`__set_terminator`、`__new_name`、`__emit_phi`、`__split_block_name` | `counter`、`func` | `context.py::FunctionEmitter` | 2（其它簇都依赖它） | 低 |
| C3 检查簇 | `__build_load`/`__build_store` 的检查部分、`__build_field_ptr`/`__build_element_ptr` 的检查部分、`__dedup`、`__merge_access`、`__invalidate_checks`、`__ptr_key`/`__pair_key`/`__value_key`、`__is_live_known`/`__is_frame_locked`、`__inherit_*`、`__root_of` | `field_derived`、`elem_derived`、`fat_root`、`raw_ptrs`、`checked`、`frame_locked`、`live_known` | `passes/{provenance,insert_checks,optimize_checks}.py`（迁移期先做 `checks.py` 组合件） | 3 | 高（§4 规则表） |
| C4 语句/控制流 | `__translate_block/if/loop/match/break/continue/defer/return/semi/let/panic/...`、`__emit_defers_to`、`LoopCtx` | `defer_scopes`、`loops` | `lower/stmts.py` | 4 | 低（忠实搬移；块创建顺序照抄） |
| C5 惰值/取址 | `__resolve_addr*`、`__build_var_ptr_fat/raw`、`__resolve_*_addr`、`__build_alloca` | （`frame_lock` 只读） | `lower/lvalues.py` | 5 | 中 |
| C6 聚合/类型构造/转换 | `__build_aggregate_construct`、`__build_array_construct`、`__build_variant_construct`、`__resolve_cast`/`__build_cast`、`__build_size_of`、`__resolve_tuple/array/array_repeat`、`__hir_pattern_to_ir` | — | `lower/aggregates.py` | 5 | 低 |
| C7 调用与系统内建 | `__resolve_call/invoke/method_call`、`__build_call/invoke/func_ptr`、`__build_sys_*`、`__resolve_open/close/sqrt/mem_copy/arg_*` | — | `lower/calls.py` + `lower/sys.py` | 6 | 中 |
| C8 内存与指针原语 | `__build_load/store/malloc`、`__build_element_ptr/field_ptr`、`__build_ptr_diff/cmp`、`__build_binary/unary` | — | `lower/memory.py`（检查部分委托 C3） | 7 | 中 |
| C9 表达式下降 | 其余 `__resolve_*`、`__resolve_val`、`__resolve_binary/logical/assign/compound_assign` | — | `lower/exprs.py` | 8（依赖 C5–C8） | 中 |

搬完之后的 `CfgBuilder`（编排者）只剩：

```python
class CfgBuilder:
    def build(self) -> IR.Function:
        emit = FunctionEmitter(...)                 # C2
        stmts = StmtLowerer(self.__ctx, emit)       # C4（内部持有 exprs/lvalues/...）
        fn = IR.Function(name=..., type_id=..., blocks=[], entry=...)
        emit.bind(fn)
        stmts.run(HIR.Block(dp.body))               # HIR → CFG IR（不含检查）
        run_pipeline(fn, self.__ctx)                # C1 + C3 的 pass
        return fn
```

**每个簇的验收**：方法与字段**成对搬走**（不留"半搬"状态）；搬完该簇后 `builder.py` 里对
这些字段的引用应为 0（可用 `grep -c 'self.__<field>'` 断言）；IR 等价按 §6.1 检查。

## 4. 规则映射（逐条）

下表是**唯一真值**：迁移时每条规则照抄，位置改变但不许改判定。

| # | 现状规则 | 现状位置 | 新归属 |
| --- | --- | --- | --- |
| R1 | raw 模式（`--raw-pointers`）：一律不插检查 | 各 `__build_*` 的 `self.__raw_pointers` 早退 | P1：pass 入口整体短路 |
| R2 | ZST 指针：无检查（`{}` 擦除） | `__is_fat_pointer` 判定 | P1：`is_zst(type_id)` 短路 |
| R3 | 裸指针（`__raw_ptrs`）：跳检查 | `__is_raw_pointer` | P1：`provenance::is_raw(reg)`（由 `VarPtr(raw)`/`Alloca(fat=False)`/`Cast(raw)` 前向传播） |
| R4 | `T&` 访问：只 `CheckRefAccess`（免 in_bounds） | `__build_load`/`__build_store` 的 RefType 分支 | P1：`RefType` → `CheckRefAccess` |
| R5 | `T*` 访问：`CheckSafeAccess(live=not live_covered)` | 同上 PointerType 分支 | P1：`PointerType` → `CheckSafeAccess(live = not proven_live)` |
| R6 | `live` 恒真三来源：帧内锁（`__frame_locked`）、刚分配（`__live_known`）、同块同出处已检查（dedup 命中） | `__is_frame_locked` / `__is_live_known` / `__dedup` | P1：帧内（`frame_word` 同一 SSA 值）+ 刚分配（`Malloc` 支配且未被 `Delete`/`Call` kill）；P2：同块去重 |
| R7 | 同块同 SSA 值已查 → 跳过（`__dedup`） | `__dedup` | P2：块内支配 + 键相等 |
| R8 | 派生链三重合并（ElementPtr→FieldPtr→Load/Store，`__elem_derived`/`__field_derived`） | `__merge_access` | P2：同规则，键从 IR 派生边重建 |
| R9 | 挂起 `CheckInBounds` 义务（one-past-end 前提），在失效点前补发 | `__checked` 的挂起项 + `__invalidate_checks` | P2：显式"义务集合"，失效点冲刷 |
| R10 | 失效触发器：`Delete`、`WriteLockSlot`、`Call`、`Invoke`、块终结符、块切换；`Delete`/调用同时清 `live_known` | `__invalidate_checks` 调用点 | P2（检查状态）+ P1（`live_known` kill 由 `provenance` 处理） |
| R11 | `__build_element_ptr`：`CheckElementArith`（良构），`in_bounds` 留到访问点 | 同方法 | P1：`ElementPtr` → `CheckElementArith` |
| R12 | `__build_field_ptr`：T& 基址 → `CheckRefAccess`；ElementAccess 走合并 | 同方法 | P1 + P2 |
| R13 | `PtrDiff` → `CheckPtrDiff`；`PtrCmp` → `CheckPtrCmp` | `__build_ptr_*` | P1 |
| R14 | `Delete` → `CheckDelete`，随后失效 | `__translate_delete` | P1（插入）+ P2（失效） |
| R15 | raw 数组访问 → `CheckRawBounds` | `__resolve_array_access` | P1 |
| R16 | 数组退化 `T[m]*→T*` → `CheckInBounds` + 静态 size=m；`T[]→T&` → `CheckSliceNonEmpty` | cast 分支 | P1 |
| R17 | syscall 边界 → `CheckViewAccess(live=not frame_locked)` | `__build_sys_read/write` | P1 |
| R18 | 方法 receiver → `CheckInBounds` | `__resolve_method_call` | P1 |

## 5. 分析设计

### 5.1 出处格（`provenance.py`）

对每个指针型 SSA 值给一个四档格（保守取并集）：

```text
Raw          裸指针（跳检查）
FrameLive    锁字段来自本函数帧字 → live 恒真
Fresh(i)     来自第 i 个 Malloc，且从该 Malloc 到此处未被 Delete/Call/Invoke kill
Unknown      其余（正常插检查）
```

传播：`VarPtr(raw → Raw)`、`Alloca(fat=False → Raw)`、`Malloc → Fresh`、`Cast(raw → Raw)`、
`FieldPtr/ElementPtr/Cast(非 raw) → 源值`；`Delete(x)`/`Call`/`Invoke` → 清掉对应
`Fresh`（调用保守清空全部 `Fresh`，与今天 `__invalidate_checks` 的行为一致）。

帧内判定：`VarPtr(frame_word=v)` 且 `v` 与本函数 `AcquireFrameLock` 的结果是同一 SSA 值
（今天用 lowering 期的集合，pass 里改成 SSA 比较，语义等价）。

### 5.2 root 等价类（去重键）

今天 `__fat_root` 记录"锁字段出处"，键 = `(root, kind)`；pass 里重建：对每条派生边
（`FieldPtr/ElementPtr/Cast`）做并查集，root = 最近的 `VarPtr`/`Malloc`/`Load` 结果。
`__pair_key(base, offset)` 的三元组键同上，用派生边 + offset 的 SSA 值构造。

### 5.3 不需要新 IR 节点

所需事实在现有节点里都有：`VarPtr(raw)`、`Alloca(fat)`、`Malloc`、`Cast(raw)`、`FieldPtr`、
`ElementPtr`、`Delete`、`Call`、`Invoke`，以及检查节点自身。唯一需要新增的是 P2 内部的
"义务集合"数据结构（不是 IR 节点）。

### 5.4 检查插入不改 CFG 结构（更正）

**现状核查**：CFG 层的检查插入**从不分裂基本块**——`cfg/builder.py` 里 `__new_block` 的调用点
只有 HIR 控制流（`if`/`loop`/`match`/`logical`/`dyn.fill`），检查路径一个都没有；`__merge_access`
也只是往当前块追加一个合取检查节点。带守卫块的形态是**LLVM 层**（`llvm/builder.py::__emit_guarded`）
按检查节点流确定性生成的。

因此：

- **忠实的 P1（只搬不改）产出的 CFG 与今天逐语句相同，LLVM IR 也随之相同**——不需要"插入顺序
  确定化"这种额外约束，只有一条平凡要求：pass 的遍历顺序确定（RPO + 块内顺序），便于阅读 diff。
- 只有**故意改变节点序列**的 pass（P9 的合并/去重/循环不变提升）才会改变 LLVM IR 与性能——
  这属于优化改动，按 §6.3 用基准把关，不属于"重构"。

## 5.5 P8 的落地设计（待执行；本轮不做）

P0–P7 之后下降侧已在 11 个模块里，检查**插入点**仍在下降簇中（经 `CheckState` 做去重/合并）。
P8 要把"插入"变成真 pass，关键约束是：**有些检查带语义上下文，单看 IR 恢复不出来**。据此分成两类：

**A 类（访问点规则，可从 IR 重建）**：R4 `T&` live、R5 `T*` safe_access、R6 live 三来源
（帧内/刚分配/已检查）、R7 同块去重、R8 派生链三重合并、R11 `ElementArith`、R12 `FieldPtr`
义务、R14 `Delete` 四前提。它们的判定只需：类型（`type_ctx`）、provenance（`VarPtr(raw)`/
`Alloca(fat)`/`Malloc`/`Cast(raw)`/`FieldPtr`/`ElementPtr` 派生边）、以及块内顺序。

**B 类（语义点，需要标记）**：R15 raw 数组上界（`CheckRawBounds`，需 length）、R16 数组退化
`CheckInBounds(size=m)` 与 `T[]→T&` 的 `CheckSliceNonEmpty`、R17 syscall 边界的 `CheckViewAccess`、
R18 方法 receiver 的 `CheckInBounds`、R13 `PtrDiff`/`PtrCmp`。

**两条路线**：

1. **路线 A（推荐，改动小）**：只把 A 类搬到 `passes/insert_checks.py`；B 类保留在下降侧照发。
   下降侧删掉 A 类的内联发射，改为"只发访问节点 + provenance 节点"；pass 按 R4–R14 逐块重建
   `elem_derived`/`field_derived` 义务表并插检查（顺序与今天一致：`ElementArith` → `InBounds`
   挂起 → live/合取检查 → 访问）。验收仍是 108/108 `cfg.txt` 等价。
2. **路线 B（彻底，改动大）**：新增一个轻量标记节点 `IR.CheckRequest(kind, operands)`，下降侧
   在所有检查点只发标记（把语义上下文——数组长度 m、syscall 边界、receiver——编码进标记），
   pass 统一决定"保留/合并/提升/删除"。收益是 P9（循环不变提升）有完整信息；代价是新增 IR 节点、
   改 `dump.py`、并要重新证明每条规则的等价性。

**决定（用户裁定）：走路线 B**。分组迁移、每组单独提交、每组都以 108/108 `cfg.txt` 等价为准入门槛。

**路线 B 进展（第 1 组，完成）**：`IR.CheckRequest{kind, operands, extra, live}` 标记节点 + 三个
kind 常量（`CHECK_REQUEST_PTRDIFF/PTRCMP/RAW_BOUNDS`）+ `passes/insert_checks.py`（按 kind 物化，
未知 kind 直接报错，避免静默丢检查）+ `run_pipeline` 顺序改为"物化标记 → C1 后处理"；
下降侧把 R13（`PtrDiff`/`PtrCmp`）与 R15（裸数组上界）三条规则改为发标记；LLVM 翻译器对漏物化的
标记直接 `ValueError`（防御管线漏 pass）。实测 108/108 `cfg.txt` 与基线逐字节一致、三套件
756/156/99 全绿、`--check --asan` 通过、pyright compiler/anx 0 errors。

**路线 B 进展（第 2 组，完成）**：R16（数组退化 `InBounds`、`T[]→T&` 的 `SliceNonEmpty`）、
R17（`CheckViewAccess`，3 处）、R18（receiver `InBounds`）共 6 个发射点改为发标记
（`CHECK_REQUEST_VIEW/SLICE_NONEMPTY/IN_BOUNDS`）；`live` 等下降期已判定的上下文先编码进标记，
等第 4 组把 provenance 分析搬进 pass 后再由 pass 自行推导。108/108 `cfg.txt` 等价、三套件全绿、
`--check --asan` 通过、pyright 0 errors。

**路线 B 进展（第 3 组，完成）**：R11/R12 的**发射点**改发标记（`CHECK_REQUEST_ELEMENT_ARITH`、
`CHECK_REQUEST_IN_BOUNDS` 的 owed-elem/base 两路，共 4 处，含"嵌套派生链先补发"的 2 处）——
判定仍由下降侧的 `CheckState` 做出、标记落在原来发射检查的位置，故 108/108 `cfg.txt` 等价。

**路线 B 进展（第 4 组，完成）**：`insert_checks` 由"标记物化器"升级为真正的检查插入 pass——
下降侧只剩"访问节点 + 语义标记"，检查的**判定与状态**整体搬进 pass，按块顺序重放下降期那套
状态机（`passes/insert_checks.py::__CheckPlanner`，281 行）：

- **出处重建**：新增 `passes/provenance.py`（`Provenance`：raw / 帧内 / 出处根 / 刚分配窗口），
  按同一套登记规则从 IR 重放——`VarPtr`/`Alloca`/`Malloc`/`Cast`/`FieldPtr`/`ElementPtr` 边 +
  `LiveKnownBegin/End` 窗口标记；`verify` 在 pass 运行期把它与下降侧仍保留的单调集合逐点比对
  （迁移期等价网，只读）。重建必须复刻下降侧的**形状差异**：`FieldPtr` 对非裸基址一律继承，
  而 `ElementPtr` 只在胖基址上继承；`Malloc` 只在胖模式登记出处；raw 模式下 `VarPtr` 的帧登记照旧。
- **义务表重建**：块内 `elem_derived`（`ElementPtr` 结果 → `(elem, base, offset)`）与
  `field_derived`（`FieldPtr` 结果 → 挂起 elem）自行维护，含嵌套派生链的"先补发"、`Cast` 沿
  identity 传播、`Call`/`Delete`/终止符处的失效冲刷与 `CheckElementAccess` 合取检查。
- **规则迁移**：R4（`CheckRefAccess` ×3：Load/Store/FieldPtr）、R5（`CheckSafeAccess` ×2：
  Load/Store + 合取合并）、R11/R12（`ElementArith`/`InBounds` 与嵌套链补发）、R14（`CheckDelete`
  + 其后失效）全部改由 pass 决定；receiver 折算前提改用独立标记
  `CHECK_REQUEST_RECEIVER_IN_BOUNDS`（去重键与其它 `in_bounds` 共享，`is_fat_pointer` 门留在 pass）。
  仍留标记的语义点：`PtDiff`/`PtrCmp` 前提、裸数组上界、视图访问、`T*→T&` 折算前提、切片构造源跨度。
- **下降侧瘦身**：`CheckState` 只剩出处三件（`raw_ptrs`/`frame_locked`/`fat_root`，供惰性左值路径的
  `Cast.raw`、裸数组上界门与视图 `live` 项使用），去重/义务/窗口/失效七件字段与 12 个方法全部删除；
  `builder.__set_terminator`/`__switch_to` 不再触碰检查状态。

实测 **108/108 `cfg.txt` 与基线逐字节一致**、三套件 756/156/99 全绿、`--check --asan` 通过、
pyright compiler/anx 0 errors。这组做完，`optimize_checks`（P9）拿到完整的"检查位置 + 出处 +
义务"信息。

**后续（分组迁移到此结束）**：路线 B 的四组已全部落地（第 1 组标记节点 + R13/R15；第 2 组
R16/R17/R18；第 3 组 R11/R12 发射点；第 4 组判定+状态整体搬移）。P8 之后剩两件事：P9 在
pass 上做循环不变检查提升/融合（攻 `churn_single`/`chase`/`towers` 三项已知回退），P10 清理
（`IR.WriteLockSlot` 死节点、`dump.py` 适配、`lockmech.py` 谓词一致性，以及移除 `insert_checks`
里作为迁移等价网的 `provenance.verify`——pass 已成为判定权威，长期保留等于每个函数多跑一遍
出处重放）。

**风险与回退**：P8 是本计划唯一高风险步骤（18 条规则、23 个发射点）。按规则分组提交，
每组都以"108/108 `cfg.txt` 等价 + 三套件 + pyright"为门槛；任一组不过即回退该组。

### 5.6 第 4 组（最后一组）为何必须原子完成

实测：下降侧原有 **~60 处**调用推进检查状态（`dedup` 13、`ptr_key` 9、`is_frame_locked` 6、
`mark_raw` 5、`merge_access` 4、`is_raw` 4、`mark_root`/`live_key`/`invalidate`/`inherit_*` 各 3…），
分布在下 6 个模块里。**判定与状态是同一个顺序机**：去重键在发射点即时登记、义务表在
`FieldPtr` 处挂起、在访问点消费、在 `Call`/`Delete`/终止符处冲刷。因此"只把状态搬进 pass"
会让 lowering 的判定看不到表（去重/合并失效 → 检查变多或变少），**必须判定+状态一起搬**。

落地的两步子步（各自独立验收，均已提交）：

1. **事实外化（已完成）**：`IR.LiveKnownBegin/End{root}` 窗口标记落地——下降侧在
   `mark_live_known`/`unmark_live_known` 处**同时**发标记（登记仍保留，判定暂不动），
   `insert_checks` 消费标记并自行维护一份窗口集合（Begin 加入 / End 移除，End 缺配 Begin 直接断言）。
   raw 语义本来就由 `VarPtr(raw=True)`/`Alloca(fat=False)`/`Cast(raw=True)`/`Malloc` 承载，无需新标记；
   帧锁出处可由 `VarPtr.frame_word` 与函数 `AcquireFrameLock` 结果比对得出。
   实测 108/108 `cfg.txt` 等价、三套件全绿、`--check --asan` 通过、pyright 0 errors。
2. **判定+状态搬移（已完成）**：`insert_checks` 按块顺序重放同一状态机，自行决定 R4/R5/R11/R12/R14
   与去重/合并/失效，下降侧只剩"发访问节点 + 语义标记"；出处重建（`passes/provenance.py`）先以
   `verify` 与下降侧单调集合逐点对照作为迁移网（见 §7 第 4 组记录）。门槛仍是 108/108 `cfg.txt`
   等价；过完这一步，P9 的循环不变检查提升才有信息可用。

### 5.7 P9 实测结论：检查**位置**没有空间，回退的代价在表示层

P9 原计划"循环不变检查提升 + 检查融合"攻 `churn_single`/`chase`/`towers` 三项回退。实测（同机
A/B，min-of-5；B1 用 `55400da` 的源码树直接编译同一基准，排除环境漂移）：

| 基准 | B | B1 | B/B1 | `live` 占 B 的比例 |
| --- | --- | --- | --- | --- |
| `AWFY/towers` | 4839.6 ms | 4555.7 ms | **+6.2%** | **43%**（锁表 load ≈25% + 分支 ≈18%） |
| `fatptr/chase` | 161.8 ms | 149.8 ms | **+8.0%** | 0% |
| `ALLOC/churn_single` | 23.4 ms | 20.3 ms | **+15.3%** | 7.3% |
| `ALLOC/churn_mixed` | 26.4 ms | 39.4 ms | −33.0% | 10.6% |
| `AWFY/json` | 1239.9 ms | 1586.3 ms | −21.8% | 9.2% |
| `AWFY/storage` | 1667.8 ms | 1750.5 ms | −4.7% | ~0 |

（`live` 占比 = 把 `check_live` 的条件临时改成恒真后的耗时差，只用于测量，不进代码。）

三条结论：

1. **"检查融合"在 CFG 层是无效功**。把 live 去重键从"分配锚点"改成"锁字段出处"（让同一指针的
   所有 `FieldPtr`/`ElementPtr`/`Cast` 派生共享 live 键，同块内后续访问只查 in_bounds），108 份
   语料的语义校验 0 违规（只允许 live 项被吸收/降级），但 9 个基准 A/B 全部落在噪声内
   （towers 4839.6 → 4875.7）。原因：**LLVM 已经对相同条件做了 CSE 与分支合并**——CFG 层少一个
   检查节点不改变机器码。
2. **"循环不变提升"没有合法空间**。热点的检查都位于条件分支内（不后支配函数入口/循环前驱），
   提到支配点会在"本来不会失败"的执行路径上**引入新的失败**，语义不允许；唯一可做的是"同一
   live 事实已在支配块验证过则后续块复用"，而那正是 LLVM 已经在做的。可安全做的是"分配后未释放
   的指针免 live"（已有 `LiveKnownBegin/End` 窗口的推广），但它只覆盖**函数内新分配**的指针，
   `towers` 的热点引用是**参数**，函数内无法证明。
3. **`chase` 的回退与检查无关**：把 `live` 改成恒真，chase 完全不动（161.8 → 165.6 ms），说明它的
   +8% 来自节点布局/足迹（`micro` 基准量的就是这件事），不在检查簇的射程内。

**因此这条回退线的唯一有效杠杆是"少做 live"或"让 live 更便宜"**，两个候选：

- **候选 A（已实现并实测：同样无效功，未保留）**：LLVM 发射层做**分支融合**——把窗口
  （连续的检查 + 纯地址运算）内所有检查的谓词合成一个热路径分支，失败时按原顺序冷路径重测、
  报原错误码（错误码与报错顺序不变；谓词在热路径算出且支配失败块，冷路径直接复用不重算）。
  实测：**热路径分支数确实下降**（`push_disk` 安全检查分支 5→3，且融合形态存活到 `-O2` 后的 IR，
  函数内分支/块总数与基线一致但热路径更短），可是 9 个基准 A/B 全在噪声内
  （towers 4868.8 → 4875.3，+0.1%；其余 ±1.6%）。机制：省下的分支与合取新增的 `and` 指令
  大致等值——**分支本身近乎免费（预测完美），代价在每条检查的指令数**（锁表下标算术 + load +
  key 比较）。代码留在 `/tmp/p9a/` 备查。
- **候选 B（尚未做，上限 ≈25%+，需表示层决策）**：让**锚定指针**的 live 直接读块头（与负载同一
  cache line、省掉下标算术），锁表只服务视图与 `del` 的锚点还原；块头仍是 8 B
  （`{extent:u32, pad}` 的 `pad` 改放 key，尺寸不变）。等于把 B1 的"锁字在块头"和 B 的"全局锁表"
  合起来，需要像 A/B 变体那样做完整对比。

**收紧后的结论**：CFG 层的检查融合（同块去重键优化）与 LLVM 发射层的分支融合**都实测为零**——
任何"检查位置/分支结构"的改动都不会改变机器码成本，因为最终检查 CFG 由 LLVM 决定、而成本在
每条检查的指令序列上。P9 若要有产出，只能走"**少做检查**"（liveness 证明：函数内新分配且未被
释放的指针可免 live；`towers` 的热点引用是参数，函数内证不出来，需要跨过程的"参数不会被释放"
约定）或候选 B（每次检查更便宜）。

`churn_single` 除掉 live 后仍比 B1 慢约 8%，同样指向表示层的其它项（块头/extent 等），不在检查射程内。

### 5.8 候选 B：设计 + 实测否决（P9 到此收口）

**目标**：堆指针的 `live` 不再"下标算术 + 512 MB 表里的一次独立 load"，改为"指针自带锚点 →
读块头里与负载同一条 cache line 的 key"。块头仍是 8 B（`{extent:u32, pad:u32}` 的 `pad` 放 key）。

**上界已经被实测过：就是 B1 配置**（key 在块头、没有锁表，代价是 16 B 头）。同机对照：

| 基准 | B（8 B 头 + 锁表） | B1（16 B 头 + 块头 key） | B1 vs B |
| --- | --- | --- | --- |
| `AWFY/towers` | 4868.8 ms | 4555.7 ms | **−6.4%** |
| `fatptr/chase` | 166.3 ms | 149.8 ms | **−9.9%** |
| `ALLOC/churn_single` | 23.4 ms | 20.3 ms | **−13.3%** |
| `AWFY/json` | 1276.6 ms | 1586.3 ms | +24% |
| `AWFY/storage` | ~1720 ms | 1750.5 ms | ±3% |
| `ALLOC/churn_mixed` | 26.4 ms | 39.4 ms | +49% |

即：**B1 赢的地方正是 live 贵的地方（三项回退），输的地方是 16 B 头的足迹代价**（json/churn_mixed）。
候选 B 的收益就是"拿到 B1 的 live、保住 B 的 8 B 头"——理论上两者兼得，而不是 §5.7 里估的"25%+"。
（§5.7 的 25% 来自"整条 load 链都不做"，把 key 语义一起去掉了；只去掉下标算术的隔离实验 E7→E8
在 towers 上 −14%，但 json 反而 +11%，噪声主导，不足以单独支撑表示层改动。）

**障碍：派生指针找不到块头**。`FieldPtr`/`ElementPtr` 产生的指针 `data` 在块内部，块头只能由**锚点**
到达，而锚点今天存在锁表项里（`anchor_lo32`）——所以"读块头 key"对派生指针会退化成"先查表拿锚点"，
白忙。towers/chase 的热点字段访问正好全是这种派生指针。

**解法：把锚点放进指针自己**（替掉锁表下标），并留一个判别位区分堆/非堆：

```text
word = ⟨key:31 | hdr:1 | anchor_or_lock:32⟩        # 方案 B3
  hdr = 1（堆）：anchor = (data & WINDOW_MASK) | 低 32 位
                 live    = word ≠ 0 ∧ load32(anchor - 4) == key     # 块头 key，与负载同行
                 is_heap = hdr                                      # 纯位判定，比下标比较更便宜
                 del / CheckViewAccess 的锚点还原也省掉锁表
  hdr = 0（帧/字面量/环境）：维持现有锁表路径（下标 = 低 32 位）
```

低 32 位放锚点是可行的：`del`/视图今天就已经用 `(data & WINDOW_MASK) | anchor_lo32` 还原锚点
（分配器保证同 4 GiB 窗口），所以这只是把同一个还原搬到 `live` 上。判别位从 key 借 1 位
（32 → 31 位代际空间；帧 SENTINEL 与 `is_heap` 判定随之调整）。

**触及面**（这是表示层改动，不是 pass 改动）：

- `lockmech.py`：`WORD_KEY_SHIFT/KEY_MASK/LOCK_MASK/WINDOW_MASK`、`BlockHeader`（`pad` → key）、
  `LockEntry`、`live`/`is_heap`/`is_raw` 谓词与各字段注释；
- `runtime/`：分配器写块头 key（不再写表项 key）、释放换代、`del` 的锚点路径、自测；
  `runtime/build.py --check` 的 ABI 常量断言（与 `lockmech.py`、`runtime_lib.py` 对齐）；
- `compiler/codegen/llvm/builder.py`：`__check_live`/`__lock_of`/`__word_key`、`is_heap` 判定、
  `malloc`/`delete`/`check_view_access`/`check_delete` 的锚点路径、帧字面量 word 常量；
- 复现 docs：`fat-pointer-representation-plan.md` §4.3 语义清单；`lockmech` 注释里的编码约定。

**验证门槛**（与 A/B 变体同口径）：三套件（756/156/99，fat+raw）+ `runtime/build.py --check --asan`
+ pyright + §4.3 语义清单（one-past、视图越界、释放与复用、跨对象指针算术/比较、raw 往返）
+ A/B：`towers`/`chase`/`churn_single` 应接近 B1（−6…−13%），`json`/`churn_mixed` 应保持 B 的水平。

**风险**：key 位宽 32→31 的代际空间（同槽复用 2^31 次后可能让悬垂指针重获匹配；与今天 2^32 同一量级，
但需要写进安全文档）；判别位与帧/字面量/环境路径的既有断言；`del` 与视图是安全敏感路径，必须逐条
对照 §4.3 清单。raw 模式完全不受影响（无锁无表）。

#### 实测否决：B3 拿不到 B1 的 live，且锁表不是 cache 问题

设计完成后补了两项判定性测量，结论是**候选 B 不成立，不改表示**：

1. **锁表 load 不产生额外 cache miss**。callgrind `--cache-sim=yes`（towers 缩到 `disks=18`，
   2^18 次移动）下把 live 的锁表读换成"读 `data` 自身行"（零地址算术的理想形态）：

   | | I refs | D1 misses | LL misses |
   | --- | --- | --- | --- |
   | 现状（锁表） | 40,642,830 | 2,141 | 3,083 |
   | 理想（地址算术为 0） | 34,746,543 | **2,141** | **3,081** |

   **miss 完全相同**（锁表槽常驻 L1），指令数 −14.5%。"512 MB 独立流"的假设被否掉：
   live 的代价是**指令数**，且上限就是"零地址算术"这一理想形态。
2. **B3 到不了这个上限**。B3 读块头 key 需要先还原锚点：`anchor = (data & WINDOW_MASK) | anch`
   （2 条指令），与现状的 `and`（取下标）+ 折叠进 load 寻址的 GEP 等值；key 仍要从 word 高位
   `lshr`/`trunc` 取出，`word ≠ 0` 的 null 短路也省不掉——**live 指令数≈持平**。
3. **B1 为什么便宜**：它的指针直接携带**绝对锁槽地址 + 64 位 key**，于是
   `live = load64(p.lock_addr) == p.key`（优化后就是 `load` + `icmp` 两条，无地址算术、无
   key 提取、无 null 短路）。这要求指针多带一个地址字——`T&` 24 B、块头 16 B。**16 B 的
   `T&` 在结构上装不下"全地址 + 全 key"**，所以 B 的紧凑（json/churn_mixed 的 −22%/−33%）
   与 B1 的便宜 live（towers/chase/churn_single 的 −6…−13%）是同一枚硬币的两面。

**P9 结论（收口）**：检查的"位置/分支结构"没有空间（§5.7 两个原型均实测为零）；"让每次检查更便宜"
绕不开 B 的编码取舍——唯一拿得到 B1 live 成本的办法就是回到 B1 的布局，而那正是已经裁定过的
取舍（B 胜出）。三项回退里 `chase` 与检查无关（去掉 live 完全不动），`towers`/`churn_single` 的
live 部分只能靠换表示换回来。**故 P9 不产出代码改动**，回退归因与取舍记录在案，转 P10。

## 6. 验收方法

### 6.1 验收口径（按用户裁定）

**不需要 IR 文本等价**：验收以测试与基准为准——三套件（含专门覆盖检查的 safety 套件）、
`--check --asan`、§4.3 语义清单、以及 §6.3 的性能基准。

IR 对比（`--dump` 的 `cfg.txt` / `-t ll`）只作为**排查工具**：当测试或基准出现意外回退时，
用它把差异定位到具体块/语句，而不是当作准入门槛。为方便排查，建议 harness 仍产出：

1. 每份源在两模式下的 `cfg.txt`（块顺序、语句顺序、检查节点种类与位置）；
2. `Check*` 节点的**计数**（每函数一行）——一条廉价的"没丢检查、没多检查"信号，
   只在人工排查时看，不做自动比对。

语料：`tests/basic` + `tests/safety` 的 537 份 `.an`（fat/raw）+ `tests/package` 的 111 份 fixture
+ `bench/*/an` 的 29 份基准源。脚本放 `/tmp`（不进仓库，遵守"不加测试"的既有约定）。

### 6.2 常规门槛

- 三套件 `scripts/run_tests.py --all -q`（756/156/99，fat+raw）全绿；
- `runtime/build.py --check --asan`；
- `fat-pointer-representation-plan.md` §4.3 的语义清单（one-past、视图越界、指针比较/算术、
  niche/布局、释放与复用、raw 往返）。

### 6.3 性能门槛（块顺序影响的唯一把关）

重构阶段预期零性能漂移（§5.4 已论证忠实搬移不改变节点序列）；一旦某个阶段**故意**改变
检查序列（P9），就必须在**干净提交**上跑 `bench_three_way --set micro/ptr/full` 与
`bench_allocator`，与上一阶段对比：中位数漂移 < 1%、单项漂移 < 3% 视为纯重构；越界必须记录，
并说明是优化带来的收益还是布局噪声（用 raw 列作对照，本仓库已实测过 ±10% 的布局效应）。

## 7. 分阶段计划

| 阶段 | 内容 | 产出/收益 | 风险 | 回退 |
| --- | --- | --- | --- | --- |
| P0 ✅ | 建 §6.1 harness（`/tmp/cfg_equiv.py`，108 份语料）并记录基线 | 迁移的安全网（记录 27 s / 比对 27 s） | 无 | — |
| P1 ✅ | C1：抽 `passes/{__init__,context,cleanup}.py`（DCE/RPO/终结保护 + `PassContext` + `run_pipeline`） | builder 1770 → **1629 行**（−141），新包 208 行 | 低（已实测忠实） | 单提交 revert |
| P2 ✅ | C2：抽 `passes/emitter.py::FunctionEmitter`（func/current_block/counter + new_name/emit/new_block/void_reg/never_reg/emit_phi/terminate/position）；`__set_terminator`/`__switch_to` 保留检查状态副作用并委托句柄 | 所有簇的共同句柄；builder 1629 → **1590 行** | 低（已实测忠实） | 单提交 revert |
| P3 ✅ | C3：抽检查簇为组合件 `passes/checks.py::CheckState`（方法 + 7 个独占字段整体搬，行为不变），`builder` 持有 `self.__checks`；发射能力由 `FunctionEmitter` 注入（invalidate 时补发挂起 `CheckInBounds`） | builder 1590 → **1442 行**；状态显式化 | 低（已实测忠实） | 单提交 revert |
| P4 ✅ | C4：抽 `lower/stmts.py::StmtLowerer`（16 个 `translate_*` + `LoopCtx` + `loops`/`defer_scopes`），表达式与检查能力经 `StmtHost`（emitter/checks/set_terminator/switch_to/resolve_val/is_del_target）注入 | builder 1442 → **1184 行** | 低（已实测忠实） | 单提交 revert |
| P5 ✅ | C5+C6 **合并**为 `lower/values.py::ValueLowerer`（26 个方法 + `frame_lock` 状态；`ValueHost` 注入 emitter/checks/type_ctx/raw_pointers/resolve_val/build_element_ptr/build_field_ptr/build_func_ptr/build_extract_value/is_fat_pointer） | builder 1184 → **884 行** | 低（已实测忠实） | 单提交 revert |
| P6 ✅ | C7+C8：抽 `lower/calls.py`（6 方法）、`lower/sys.py`（12）、`lower/memory.py`（10，内存原语的检查部分仍写在这里、状态经 `checks`）；三簇各自的 Host 注入；`ValueHost` 的三处内存回调用晚绑定 lambda 打破构造环 | builder 884 → **568 行** | 低（已实测忠实） | 单提交 revert |
| P7 ✅ | C9：`lower/exprs.py::ExprLowerer`（`resolve_val` 分派器 + 14 个表达式解析）+ 判定簇 `passes/predicates.py::PtrPredicates`（3 个判定）；builder 只剩 `__init__/build/__set_terminator/__switch_to/__build_func_ptr` | builder 568 → **189 行**（目标 <200 达成），结构目标达成 | 低（已实测忠实） | 单提交 revert |
| P8 ✅ | C3 升级为真 pass：下降不再发访问类 `Check*`，`passes/insert_checks.py` 接管判定与状态 | 路线 B 四组落地；`CheckState` 瘦身为出处三件；插入逻辑可独立演进 | **高** | 按规则分组小步提交（四组均已提交）；任一步测试或基准回退即回退该步 |
| P9 ✅（结论：不改代码） | 检查优化。**三项实测否决**：CFG 层融合 0、发射层分支融合 0（分支近乎免费，成本在指令）、候选 B 0（callgrind 证明锁表不产生 cache miss；B3 的锚点还原抵消收益，B1 的便宜 live 需要 16 B 指针，装不下）。三项回退是 B 的 8 B 头编码取舍的另一面，唯一解法是回到 B1 布局（已裁定） | 回退归因 + 表示层取舍记录（§5.7/§5.8）；不产出代码 | — | 无代码改动 |
| P10 | 清理遗留：`WriteLockSlot` 死节点、`dump.py` 适配、`lockmech.py` 谓词一致性、移除迁移用 `provenance.verify` | 去死代码 | 低 | — |

**进展（P0+P1 完成，第 10 轮）**

- P0：`/tmp/cfg_equiv.py` 对 108 份语料（tests 采样 + safety 全部 + bench 29 份）跑
  `yianc --dump -t none`，记录 `cfg.txt` 与每函数 `check_*` 计数；record/compare 各约 27 s。
- P1：三个后处理过程（`__eliminate_dead_code` / `__sort_blocks_rpo` / `__guard_termination`）
  整体搬进 `passes/cleanup.py`，`PassContext`（type_ctx/symbol_ctx/raw_pointers/func_name/span/
  return_type/new_void_value）与 `run_pipeline` 落在 `passes/{context,__init__}.py`；
  `CfgBuilder.build()` 现在只做"下降 + 调用 `run_pipeline`"。搬移忠实度实测：
  **108/108 份 `cfg.txt` 与基线逐字节相同**。
- 顺手清理：删掉 pyright 报的死方法 `CfgBuilder.__extract_fat_field`（无调用者）与
  `lockmech.KEY_BITS` 重复定义；修掉 B 提交里 9 处 pyright 报错（注释/注解级改动）。
  现在 `pyright compiler` 与 `pyright anx` 均为 **0 errors**。
- 门槛：三套件 756/156/99 全绿、`runtime/build.py --check --asan` 通过、micro 基准无漂移
  （`chase` 156.8–159.7、`copy_struct` 152.1–152.5，与 P1 前一致）。

**P2 进展（完成）**：`FunctionEmitter` 落在 `passes/emitter.py`（76 行），builder 内
`self.__emit(`/`__new_name()`/`__new_block(`/`__emit_phi(`/`__void_reg()`/`__never_reg()` 与
`self.__func`/`__current_block`/`__counter` 共 ~179 处调用点改为 `self.__emitter.*`；
块终结/块切换的检查状态清理仍留在 builder（句柄无副作用）。实测 108/108 份 `cfg.txt`
与基线逐字节一致；pyright compiler/anx 0 errors；三套件 756/156/99 全绿。

**P3 进展（完成）**：`passes/checks.py::CheckState`（237 行）接管
`raw_ptrs`/`frame_locked`/`live_known`/`fat_root`/`checked`/`elem_derived`/`field_derived`
七个字段与全部判定（`is_raw`/`mark_*`/`root_of`/`inherit_*`/`live_key`/`ptr_key`/`pair_key`/
`dedup`/`note_element_derived`/`note_field_derived`/`propagate_field_derived`/
`pop_owed_in_bounds`/`elem_entry`/`merge_access`/`invalidate`/`clear_block`）；
builder 侧只留调用（`self.__checks.*`），检查的**插入点**仍在下降侧（P8 才升级为真 pass）。
`builder.py` 1590 → **1442 行**。忠实性实测：108/108 份 `cfg.txt` 逐字节一致；三套件
756/156/99 全绿；runtime --check --asan 通过；pyright compiler/anx 0 errors。

**踩坑记录（供后续阶段）**：脚本化搬移时出现过两类错误——(1) 按行号定位做多处替换，
前面的替换会移动后续行号（一次 `__init__` 被切出错位、重复块）；(2) 用 `s.index(start)` /
`s.index(end)` 时若 end < start，`s[:start] + new + s[end:]` 会**复制**中间整段（本次
`__init__` 因此出现重复字段块），改为先断言 `start < end` 或改用唯一文本锚点。两处都是
"IR 等价 harness 立刻报出 difference（4/108）+ 探针定位到一个 unbound 的 emitter 实例"才
收敛的——印证了 §6.1 把它当排查工具的用法。

**P4 进展（完成）**：新建 `compiler/codegen/cfg/lower/stmts.py`（322 行）——
`StmtLowerer` 持有 16 个 `translate_*` 方法与循环栈/defer 作用域栈，`StmtHost` 是它向构建器
借用的能力（emitter/checks/set_terminator/switch_to/resolve_val/is_del_target）；构建器的
分派器与 `build()` 改为 `self.__stmts.*`，`builder.py` 1442 → **1184 行**。忠实性实测：
108/108 份 `cfg.txt` 逐字节一致；三套件 756/156/99 全绿；runtime --check --asan 通过；
pyright compiler/anx 0 errors。

**踩坑记录（第二次）**：这次先用"行号区间删除"又把 `__resolve_val` 的头部切掉了（前一次
的教训没记住）——已改为**按方法文本正则切片**（`\n    def NAME\(.*?(?=\n    def |\Z)`，逐个断言
命中数 == 1）并每步 `compileall` + harness 复核。结论写进流程：**搬移一律文本锚点、禁止行号**。

**P5 进展（完成）**：`compiler/codegen/cfg/lower/values.py`（378 行）——C5 惰值/取址与 C6
聚合/转换**合并**成一个 `ValueLowerer`：原计划分两个模块，但两者双向引用（`resolve_*_addr` 要
`build_cast`，`build_cast` 要 `checks`/`is_fat_pointer`）且共用同一份 host 面，分开会得到两个互相
持有的 host，故合并（计划表已按实际改写）。帧锁实体化状态随之搬入，构建器只读
（`build()` 里 `IR.Function.frame_lock = self.__values.frame_lock`）。`builder.py` 1184 → **884 行**。

忠实性实测：108/108 份 `cfg.txt` 逐字节一致；三套件 756/156/99 全绿；runtime --check --asan 通过；
pyright compiler/anx 0 errors；micro 无漂移（chase 160.8 / copy_struct 155.6）。

**踩坑记录（第三次，已固化为流程）**：搬移生成器这次连着犯两个小错——(1) 只改了调用点没改
**方法定义名**（`def __resolve_addr` 留在原位 → `AttributeError: no attribute 'resolve_addr'`）；
(2) 去私有名的正则把 `__init__` 也改成了 `init`（构造器失效）。两条都靠"最小复现用例 + pyright"
在 1 分钟内暴露。**流程补充**：去私有化时显式白名单 `__init__`；生成后立刻 `grep 'def '` 检查
定义名与调用名一致，再跑 harness。

**P6 进展（完成）**：`lower/calls.py`（118 行，`CallsLowerer` + `CallsHost`，含 `receiver_ref_type`）、
`lower/sys.py`（93 行，`SysLowerer` + `SysHost`）、`lower/memory.py`（250 行，`MemoryLowerer` +
`MemoryHost`）；`builder.py` 884 → **568 行**。构造顺序：emitter → checks → values → memory/calls/sys；
values 需要 memory 的三个回调、memory/calls 需要 values，用三处晚绑定 lambda 打破环（已注释说明）。

忠实性实测：108/108 份 `cfg.txt` 逐字节一致；三套件 756/156/99 全绿；runtime --check --asan 通过；
pyright compiler/anx 0 errors；micro 无漂移。

**踩坑记录（第四次）**：生成器把"定义名去私有化"的正则写在 transform 里但没生效（写文件时用了
未变换的副本），表现为 `MemoryLowerer has no attribute build_malloc`；另外新模块的 `Type`/
`BinaryOperator`/`default_literals` 等导入、以及 `sys.py` 里不再使用的 `HIR`/`CompilerLog`/
`_ch_block`，靠 pyright 的 unused-import 全数点出。**流程补充**：去私有化后立刻 `grep 'def '`
核对定义名；生成后先跑 pyright 清 unused，再跑 harness。

**P7 进展（完成，结构目标达成）**：`lower/exprs.py`（388 行，`ExprLowerer` + `ExprHost`：分派器
+ 14 个表达式解析）与 `passes/predicates.py`（65 行，`PtrPredicates`：`is_fat_pointer`/
`is_fat_view`/`is_del_target`）。**`builder.py` 568 → 189 行、只剩 5 个方法**
（`__init__` 装配、`build` 编排、`__set_terminator`/`__switch_to` 块管理、`__build_func_ptr` 叶子原语），
即计划 §3.2 想要的"编排者"形态。构造顺序：emitter → checks → preds → values → memory → calls → sys →
stmts → exprs；两处构造环（values↔memory、stmts↔exprs）用晚绑定 lambda 打破，代码里都有注释。

忠实性实测：108/108 份 `cfg.txt` 逐字节一致；三套件 756/156/99 全绿；runtime --check --asan 通过；
pyright compiler/anx 0 errors。

**P0–P7 累计**：builder 1770 → **189 行（−89%）**；新模块 2151 行（`passes/` 581 + `lower/` 1570）。

**P8 收尾（完成）**：路线 B 四组全部落地后，`insert_checks.py` 从 69 行的标记物化器长成 281 行的
检查插入 pass，新增 `passes/provenance.py`（153 行）做 IR 侧出处重建与迁移期对照校验；下降侧净减
约 300 行判定代码（`CheckState` 193 → 96 行）。`--dump` 的 `cfg.txt` 在全部 108 份语料上与 P8 之前
逐字节一致，说明"判定+状态搬移"是忠实搬移；`optimize_checks`（P9）因此第一次能拿到完整的
"检查位置 + 出处 + 未偿义务"信息。

**P9 调研（结论见 §5.7，本轮）**：先做了"检查融合"原型（live 去重键改为锁字段出处），语义校验
108/108 只降不增，但 9 个基准 A/B 全在噪声内——**LLVM 已经做了同条件的 CSE/分支合并**，CFG 层
少一个检查节点不改变机器码，故该原型**未保留**（代码留在 `/tmp/p9/` 备查）。随后用三点测量把三项
回退完全归因：`live` 的代价（`towers` 43%、`json` 9%、`churn_single` 7%、`chase` 0%），B1 对照
（直接编 `55400da` 的树）复现了 `+6.2% / +8.0% / +15.3%` 与 `−33.0% / −21.8%` 的全部记录值。
随后按此实现了候选 A（发射层分支融合）并实测：热路径分支数确实下降且存活到优化后 IR，
但 9 个基准 A/B 仍全在噪声内（towers +0.1%）——省下的分支与合取新增的 `and` 等值，
**代价在每条检查的指令序列上，不在分支**。两个原型均未保留（代码在 `/tmp/p9/`、`/tmp/p9a/`）。
结论：检查的"位置/分支结构"没有空间，唯一有产出的方向是"少做检查"或"让每次检查更便宜"，
即候选 B（锚定指针的 live 改读块头 key，块头 8 B 不变）或跨过程的参数不会被释放约定。
顺带把 `--dump` 的检查节点补上 live 标记（此前恒显示 `live ∧ in_bounds`，看不出 `live=False`），
这次的归因正是靠它才看得到。

**踩坑记录（第六次，本轮）**：把出处重建做成"只读规则"时，两处**形状差异**只有靠对照校验才暴露——
(1) `VarPtr` 在 raw 模式下帧锁不实体化（`frame_word=None`），下降侧却照旧登记帧内/出处；
(2) `FieldPtr` 对非裸基址一律继承出处，而 `ElementPtr` 只在胖基址上继承（ZST 基址两边都不记）。
第一版重建按"`is_raw` 否则继承"统一处理，`verify` 立刻在 raw 三套件里报出
`provenance(frame) mismatch`（正是这一步把 175 个 `@raw` 用例一次性打挂的原因）。教训：**重建必须
逐条复刻原判定的分支形状，而不是复刻它的语义意图**；`verify` 这类对照网的价值恰在于此。另外
`__materialize` 在类体内被名字改写（`_CheckPlanner__materialize`）报 `NameError`，符合既有的
"类体引用模块级私有必须单下划线"约定。

**踩坑记录（第五次）**：又出现"生成器里写好的 def 去私有化没落到文件"（同 P6），并且构造顺序把
`ExprLowerer` 放在 `StmtLowerer` 之前（前者引用后者）；另有搬移后的导入缺失/多余一批，全部由
最小复现 + pyright 点出。**流程最终版**：生成后先 `grep 'def '` 核对定义名、再跑 pyright 清 unused、
最后 harness；构造顺序按"被依赖者先建、环用晚绑定 lambda"固定。

**附：运行时自测偶发失败已定位并修复（本轮）**：`runtime/selftest/selftest.c::run_fail_child`
对管道只做**一次 `read()`**，而 panic 路径分多次 `write`（前缀/消息/换行），于是父进程可能只读到
第一段，`strcmp(buf, "yian: panic: test-message\n")` 偶发失败——这就是此前 `--check --asan`
时而报 "panic writes prefix, message and newline" 的原因（与编译器/表示改动无关）。改为循环读到
EOF 后再比较：普通自测 12/12、ASan 自测 15/15 连续通过，`--check --asan` 连跑 3 次全绿。

**顺序理由**：C1/C2 是叶子与句柄，先立接口；C3 的字段独占性最强、收益最大，所以放在"行为不变"
的形态先搬（P3），把它升级为真 pass（P8）留到句柄与测试网都稳了之后。P4–P7 按调用依赖自外向内
（语句 → 惰值 → 聚合 → 调用/内存 → 表达式），保证每一步搬的都是"只向下依赖"的层。

## 8. 风险与开放问题

1. **决策迁移的等价性是最大风险**（P8 已消化）：18 条规则里任何一条漏掉都会变成"少检查"
   （安全问题）或"多检查"（性能回退）。把关 = 三套件（safety 专门覆盖检查）+ `--check --asan`
   + 语义清单；IR/`Check*` 计数对比只在出问题时用来定位（§6.1）。四组迁移都以 108/108
   `cfg.txt` 逐字节等价收口；P8 之后剩下的等价性风险转移到 P9 的**故意**序列改动上。
2. **故意改变检查序列会改 LLVM 布局**（本仓库实测过 ±10% 的布局效应）：这属于 P9 的优化改动，
   用 §6.3 的基准把关；纯重构阶段（P1–P8）按 §5.4 预期零漂移，不需要为块顺序设约束
   （P8 第 4 组实测 micro A/B 漂移 ≤1.2%）。
3. **`__live_known` 的 kill 语义**：只在 `dyn[n] value` 的填充循环窗口内登记（封闭区域、
   不含 `del`/调用）；P8 用 `IR.LiveKnownBegin/End` 把窗口显式写进 IR，pass 只按标记开合，
   不顺手扩大成"整个函数"（会不安全）。
4. **raw 模式与 ZST 特例**：pass 侧的 `is_fat_pointer`/`is_del_target` 复刻下降侧的两个 raw 守卫，
   规则表里不散落条件。
5. **pyright strict**：模块级私有常量/函数用双下划线；跨类共享的状态必须放 `PassContext`
   或 dataclass，不能靠名字改写访问（类体内引用模块级私有须单下划线，见 P8 踩坑记录）。
6. **已决**：`CheckState` 先"组合进 `CfgBuilder`"保持行为不变（P3），P8 第 4 组把判定与状态
   升级为独立 pass，下降侧只留出处三件与语义标记。

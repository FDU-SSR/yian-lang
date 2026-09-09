# SecL 内存安全机制：编译器与运行时实现

本文说明 [`security.md`](security.md) 的语义如何落实到当前代码。描述以仓库中的实际实现为准，
不再使用早期原型的 tree-sitter、Lian、GIR、统一三字段指针或旧 `MemoryBlock` 模型。

## 1. 编译流水线

当前完整流水线为：

```text
.an source
  -> Lexer -> Tokens
  -> Parser -> AST
  -> semantic passes and TypeCheck -> typed HIR
  -> CfgTranslator -> CFG IR
  -> LLTranslator -> LLVM IR
  -> LLVM optimization/backend + clang link -> native executable
```

`compiler/main.py` 显式组织这些阶段。类型信息由 `compiler/analysis/ty` 维护；类型检查和 HIR
构造位于 `compiler/analysis`；安全检查在 `compiler/codegen/cfg` 变成一等 CFG 节点，再由
`compiler/codegen/llvm` 降为支配实际访问的比较、条件分支和 trap。

## 2. 类型与表示

类型检查器区分三个只允许向下转换的层级：

```text
T*  ->  T[]  ->  T&
 \--------------> /
```

- `T*`：40B LLVM 聚合 `{T*, i64*, i64, i64, i64}`，字段为
  `{data,lock_ptr,key,index,size}`；
- `T[]` 和 `str`：32B 聚合 `{T*, i64*, i64, i64}`，字段为
  `{data,lock_ptr,key,size}`；
- `T&`：24B 聚合 `{T*, i64*, i64}`，字段为 `{data,lock_ptr,key}`。

字段下标和块头常量集中在 `compiler/codegen/cfg/lockmech.py`，LLVM 类型映射位于
`compiler/codegen/llvm/types.py`。函数参数、返回值、Phi、聚合构造与提取都使用同一静态 LLVM
结构，因此表示沿调用和控制流合并传播。

## 3. 转换安全

HIR 类型检查只允许合法的降级方向；CFG 构建器在丢弃空间元数据前发射必要检查：

| 转换 | CFG 前提 | LLVM 结果 |
| --- | --- | --- |
| `T* -> T[]` | `CheckInBounds` 的 one-past 合法前提和无回绕地址计算 | `data'=data+index*|T|`, `size'=size-index` |
| `T* -> T&` | `CheckInBounds(ptr,1)`，即 `index<size` | `{data+index*|T|,lock,key}` |
| `T[] -> T&` | `CheckSliceNonEmpty`，即 `size>=1` | `{data,lock,key}` |

这三类转换都原样继承 `lock_ptr/key`。转换验证的是引用或切片的空间来源，不在转换点重复
`live`；真实引用访问由 `CheckRefAccess` 检查锁。因此失效指针转换后仍然失效。

`T* -> T[]` 必须缩短为当前位置后的剩余切片。保留原 `size` 会让非零偏移转换后的末端访问
越过原对象；对应正负回归位于 `tests/fat/`。one-past 指针可传递，但转引用会 trap；空切片也
不能产生引用。

## 4. CFG 检查节点与形式规则

主要节点定义在 `compiler/codegen/cfg/ir.py`：

| CFG 节点 | 证明义务 | 典型消费者 |
| --- | --- | --- |
| `CheckSafeAccess` | `live && in_bounds` | 普通胖指针 load/store |
| `CheckInBounds` | 单元素位置存在且算术无回绕 | 解引用、字段派生、`T*->T&` |
| `CheckSliceNonEmpty` | `size>=1` | `T[]->T&` |
| `CheckRefAccess` | `live`；空间由引用来源不变量保证 | `T&` load/store |
| `CheckElementArith` | 候选索引可表示且位于 `[0,size]` | 指针加减 |
| `CheckElementAccess` | 合并验证索引和实际访问跨度 | 索引访问 |
| `CheckRawBounds` | 标准库裸片段构造的显式范围 | 受限构造路径 |
| `CheckPtrDiff` | 同源、整除和结果可表示 | 指针差 |
| `CheckPtrCmp` | 关系比较的同源约束 | `<,<=,>,>=` |
| `CheckDelete` | `live && is_heap && is_raw` | `delete` |

CFG 构建器把检查放在相关访问之前，并在可能使已有证明失效的控制流/写操作处清除局部去重
状态。LLVM builder 把每个检查展开成成功块与 trap 块；成功块支配对应 load/store。引用的空间
证明不是 `CheckRefAccess` 自己完成的，而是 §3 转换检查和取址规则共同建立的来源不变量。

## 5. 指针算术、比较和访问

`ElementPtr` 保留锚点 `data`，只更新元素单位的 `index`。`CheckElementArith` 允许 one-past，但
拒绝负结果、超过 `size` 的结果以及所有有限位宽回绕。最终地址计算使用 pointee 的 ABI 大小。

相等/不等比较不访问内存。关系比较和 `PtrDiff` 在 CFG 层验证同源，LLVM 层提取字段后比较；
异源关系比较或相减 trap。`FieldPtr` 使用类型布局派生子对象位置，仍继承根对象的锁。

load/store 的检查按表示分级：`T*` 和由切片派生的指针执行空间与时序检查，`T&` 执行时序
检查。后者成立的前提是所有引用构造点已经通过 §3 的单元素存在性检查；新增引用来源时必须
同时扩展该审计清单和回归测试。

## 6. 单线程自管堆池

`compiler/codegen/cfg/lockmech.py::BlockHeader` 固定声明 32B ABI 布局：

```text
+0  lock      u64
+8  capacity  u64
+16 next      i8*
+24 reserved  u64
+32 payload
```

`compiler/codegen/llvm/module.py` 为 fat/nocheck 模块生成：

- 内部全局 `__secl_pool_head`；
- `__secl_pool_alloc(payload_bytes)`：first-fit 查找容量足够的空闲块，不够时向 libc 请求
  `BlockHeader.BYTES + payload_bytes`；
- `__secl_pool_release(header)`：把块插入空闲链，绝不调用 libc `free`。

`LLBuilder.malloc` 从池取得块头，生成新键并写入头部，再返回从 `header+32` 开始的胖指针。
`LLBuilder.delete` 在 `CheckDelete` 之后先写 `SENTINEL`，再交给 pool release。块复用必须写入新
键；容量可以大于本次请求，但返回指针的逻辑 `size` 只反映请求对象，因此多余容量不可访问。

空闲链没有同步，当前仅适用于单线程。池不拆块、不合并块且进程期不返还 libc，这是驻留内存
和内部碎片的明确代价。

## 7. 键生成与稳定帧锁协议

`KeyGen` 的编译期模型及 LLVM 模块中的 `__gen_key_value` 都只实现单调计数器。最高位区分堆/栈；
堆键体上界是 `2^63-2`，从而排除全 1 的 `SENTINEL`。生成器在增量前检查上界，耗尽时调用
trap，不能自然回绕。

模块保留 `2^20` 个 `u64` 槽位和一个活动深度游标。槽位数组位于 BSS，地址在进程期间
保持稳定，保留 8 MiB 虚拟地址空间，按峰值活动取址深度触及页面。函数的首个胖取址使 CFG
在入口放置 `GenKey` 和 `AcquireFrameLock`；LLVM 检查深度上界后直接索引槽位，写新键再
递增深度。该帧所有取址值共享此锁。

CFG/LLVM 返回收尾确保每条正常返回路径先写 `SENTINEL` 再递减深度。优化产物中的
哨兵写受 volatile 保护。槽位永不作为用户对象；顺序或递归复用均在用户步之前 re-key，
因此旧胖指针不会恢复。超过 `2^20` 个同时活动的取址帧时确定性 trap。

## 8. 受限操作与标准库 TCB

用户代码不能直接调用会绕开表示和来源规则的原语，例如任意 `bitcast`、`from_raw_parts`、裸
系统调用封装及内部内存复制入口。编译器的受限操作检查按源码归属拒绝这些能力；标准库和明确
的标准库测试 harness 属于经过审计的例外。

这不是运行时动态类型验证。安全性结论依赖编译器拒绝用户伪造胖指针，以及标准库
对每个受限操作保持长度、布局和生命周期前提。扩展标准库受限原语等价于扩展 TCB，必须同时
补充审计说明和负面测试。

## 9. 三种编译模式

| 模式 | 表示 | 堆分配 | 帧锁 | CFG 安全检查 | 安全定理 |
| --- | --- | --- | --- | --- | --- |
| `check` | 40/32/24B | SecL 堆池 | 有 | 有 | 适用 |
| `nocheck` (`--no-fat-checks`) | 40/32/24B | 同一 SecL 堆池 | 有 | 省略 | 不适用 |
| `raw` (`--raw-pointers`) | 裸指针；切片 `{data,size}` | libc malloc/free | 无 | 无 | 不适用 |

三种模式之间的性能差异可分解为：

- `nocheck/raw`：胖表示、调用搬运、堆池和锁协议的组合成本；
- `check/nocheck`：CFG 检查发射成本；
- `check/raw`：完整机制相对裸基线的总成本。

`nocheck` 不是“除了检查外完全等同 C”的基线，`raw` 也不是安全配置。

## 10. 回归与审计

核心命令（使用项目包含 llvmlite 的 Python 环境）：

```bash
python scripts/run_tests.py -q
python scripts/run_fat_tests.py -q
python scripts/run_raw_tests.py -q
python scripts/run_fat_cve.py -q
python tests/unit/test_lockmech.py
python scripts/check_raw_free_ir.py
python scripts/check_frame_lock_ir.py
pyright --pythonpath <project-python>
```

安全回归至少覆盖：

- 非零偏移 `T*->T[]` 的剩余长度与末端拒绝；
- one-past `T*->T&` 和空 `T[]->T&`；
- 释放、复用后的 UAF 与双重释放；
- 不同容量请求的 first-fit 复用及逻辑边界；
- 堆/栈键边界、`SENTINEL` 保留值和确定性耗尽；
- 稳定帧锁影子栈的递归 LIFO、顺序复用、容量检查、退出顺序和 raw 模式省略；
- CVE vulnerable/fixed 成对用例，覆盖越界、UAF、双重释放和栈悬垂等问题。

测试总数和静态检查结果必须从当前提交现场生成，不能沿用历史文档中的计数。

## 11. 实现局限

- 堆池驻留、first-fit 内部碎片和线性查找可能成为长运行程序的成本；
- 当前没有并发分配或原子锁协议；
- 帧锁影子栈固定保留 8 MiB 虚拟地址空间，最多容纳 `2^20` 个同时活动的取址帧；
- 栈失效是帧级而不是词法作用域级；
- 胖指针不保持 C ABI，FFI 必须封送且不在证明范围内；
- 标准库受限原语、LLVM 优化正确性和元数据不可伪造均是可信假设；
- zero-sized type 和字符串字面量采用专门表示路径，新增操作时需单独审计；
- 形式证明为纸面证明，尚未在 Coq、Lean 或 Isabelle 中机械化。

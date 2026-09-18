# 内存机制与运行时改造计划

## 1. 目标与范围

### 1.1 目标

1. 把堆分配器从"单链首适配、不分割、不归还"换成**尺寸类 arena**，消除混合尺寸长跑下的病态行为与内存只涨不还。
2. 把块头从 32 B 缩到 **16 B**（`lock` + `active_size`），提高小对象密度、缩短访问路径。
3. 把运行时从"在编译器里手拼 IR"改成**独立编译的运行时库**：C 源码为唯一真值，编译为目标文件（`.o`/`.a`）后由 clang 在链接步骤并入；热路径检查仍由编译器内联发射。

### 1.3 前置

**前置已满足**：工具链为 LLVM 22（`llvmlite 0.49` + `clang 22`，安装见 `scripts/setup_llvm_toolchain.sh` 与 `AGENTS.md`）；三套件与基准已在 LLVM 22 下复测（traversal 的 fat/raw 比值：`-O0` 4.43×、`-O2` 1.38×，低于 LLVM 15 时的 6.0×/1.5×）。后续阶段以该工具链为基线。

### 1.2 不做

- 不改胖指针表示（`T*` 40 B / `T[]` 32 B / `T&` 24 B / raw 8 B）、不改检查清单与错误码。
- 不引入多线程（沿用 `security.md` §10 的单线程假设）。
- 不做 GC、不做并发标记、不做跨过程逃逸分析。
- 不改栈对象、字符串字面量、argv 三者的锁机制（仍是影子栈/全局锁槽）。

## 2. 现状与依据

### 2.1 现状构成

| 组成 | 位置 | 规模 |
| --- | --- | --- |
| 块头 | `compiler/codegen/cfg/lockmech.py::BlockHeader` | 32 B，`{lock, capacity, next, active_size}` @0/8/16/24 |
| 堆池 | `compiler/codegen/llvm/module.py::get_pool_alloc/get_pool_release` | 手拼 IR 约 120 行 |
| 运行时其余部分 | 同文件：`argc/argv` 全局、env 锁、字面量锁、键计数器、`runtime_fail`、`panic`、帧锁 arena、wrapper `main` | 手拼 IR 约 220 行 |
| 检查 | `compiler/codegen/llvm/builder.py` | 访问路径一次锁字 load + 寄存器比较；delete/view 另读 `active_size` |
| 发射 | `compiler/codegen/llvm/emit.py` | llvmlite 解析 → pass pipeline → obj/asm/bc；`.o` 由 `compiler/main.py` 调 clang 链接 |

`module.py` 共 533 行，其中约 340 行是手拼 IR 的运行时（包括用 IR 拼出来的 panic 消息写入循环）。

### 2.2 分配器实测（同一组负载，每格独立进程）

| 实现 | 同尺寸周转 | 混合尺寸长跑 | 长跑足迹 | 增长-释放归还 |
| --- | --- | --- | --- | --- |
| 现状：单链首适配、不分割、不归还 | **2.40 ns/op** | **2540 ns/op** | 58 → 252 MB（膨胀 4.3×） | **0%** |
| 尺寸类 arena（16 B 头） | 4.13 | 39.2 | 57.8 → 59.5 MB | **78%** |

现状的首适配平均每次扫过 **156 个空闲块**（每块一次 cache miss），这是长跑性能问题的主因；该问题与指针布局无关，必须独立修掉。

### 2.3 访问路径实测（按对象尺寸）

| 负载 | 现状（32 B 头） | 16 B 头 | 8 B 头 | 锁槽独立 |
| --- | --- | --- | --- | --- |
| 8 B：scan / rand | 2.09 / 10.62 ns | 1.46 / 8.64 | **1.06 / 7.42** | 1.45 / 16.20 |
| 16 B：scan / rand | 1.11 / 9.32 | 0.74 / 7.39 | 0.74 / **7.24** | 0.75 / 12.68 |
| ≥256 B | 全部收敛（元数据占比 < 12%） | | | |

结论：锁字留在块内、与负载同区；64 B 对齐方案否决（小对象上明显更差）。

## 3. 决策

1. **采用 16 B 块头**：`lock@0`、`active_size@8`、`data = lock_ptr + 16`，块 16 B 对齐。`capacity` 删除（尺寸类隐含），`next` 移到空闲块的负载区。
2. **分配器用尺寸类 arena**，参数见 §5。
3. **运行时做成独立库**：C 源码 → 目标文件（`.o`/`.a`）→ 由 clang 在链接步骤并入；不把运行时 IR 并进 LLVM 模块（版本约束见 §4.3）。热路径检查不搬出编译器。
4. **锁语义全部留在编译器侧**：键生成、锁槽写入、`SENTINEL` 失效、`live` 判定都不进运行时库；运行时只负责内存。

## 4. 运行时边界与 ABI

### 4.1 留在编译器内联发射

| 项 | 原因 |
| --- | --- |
| 访问路径检查（`live` 的 load + 比较 + 分支、边界比较） | 必须能被 LLVM 内联与提升（`-O2` 下从 6.0× 降到 1.5× 就来自这里） |
| `__load_active_size` 的守卫加载 | delete/view 冷路径，但需要与检查同一基本块形状 |
| 帧锁进入/退出（push + re-key、每条 return 前写 `SENTINEL` + pop） | 分布在所有返回路径上，且是几次存储 |
| 胖指针构造/字段提取、`is_raw` 纯字段判定 | 纯值操作，无调用 |

### 4.2 移入运行时库

| 项 | 说明 |
| --- | --- |
| `yian_rt_alloc(uint64_t bytes) -> void *` | 返回**块基址**（= `lock_ptr`）；负载 = 基址 + 16 |
| `yian_rt_release(void *block)` | 归还整块；幂等与合法性由编译器的 S006 检查保证 |
| `yian_rt_next_heap_key() / yian_rt_next_stack_key()` | 单调键与耗尽策略（R003）集中一处 |
| `yian_rt_fail(uint32_t code)` / `yian_rt_panic(const uint8_t *, uint64_t)` | `noreturn` + `cold`；只 `write(2, …)` + `_exit`，**不分配内存** |
| `yian_lit_lock`、`yian_secl_frame_locks[]`、`yian_secl_frame_depth` | 全局对象与 BSS 段 |
| `yian_rt_argc/yian_rt_argv` 与 wrapper `main` | 参数 ABI 校验（`argc >= 0`、`argv != null`）可整体搬 C |

### 4.3 交付形式与 LLVM 版本约束

运行时以**独立目标文件**（C 源码 → `.o` / `.a`）交付，由 `compiler/main.py::__link_object` 的 clang 调用一并链接：

- `-t exe`：`clang user.o runtime.a -o exe`（现状的链接步骤加一个输入）。
- `-t obj`：`clang -r user.o runtime.o -o out.o`，保持单文件自包含。
- `-t bc` / `-t ll`：运行时不并入 LLVM 模块，符号以外部声明出现；文档给出链接命令。

**不采用"把运行时 bitcode 链接进 LLVM 模块"**，原因是版本耦合已实测为硬约束：

| 路径 | 实测结果 |
| --- | --- |
| `link_in` 手写的 LLVM 15 风格文本 IR（typed pointer）+ 现有 `-O2` 管线 | 小函数被内联；`noinline` 保持调用；`cold`/`noreturn` 保留 |
| 同一条 IR 在 `-O0` | 不内联（预期） |
| 系统 clang（LLVM 18）生成的 bitcode | `parse_bitcode` 报错：`Opaque pointers ... Producer LLVM18.1.8 Reader LLVM 15.0.7` |
| 系统 clang 生成的文本 IR | 解析失败：`ptr type is only supported in -opaque-pointers mode` |

llvmlite 0.44 内部是 LLVM 15.0.7（typed pointer），系统 clang 是 18/20（opaque pointer）。因此在本机当前版本下，**跨边界内联的唯一可行形式是用 LLVM 15 风格 IR 写运行时并 `link_in`**；用 C 生成的 bitcode 进不了 llvmlite 的管线。**升级 llvmlite 后这条限制消失**（见 §6 P-1）：llvmlite 0.45 起为 LLVM 20、0.48 起为 LLVM 22，实测 LLVM 22 可以读入 clang-18 生成的 bitcode、`link_in` 后由新 PM 内联。但 producer ≤ reader 的约束长期存在，所以默认路线仍是目标文件链接：

| 层 | 形态 | 理由 |
| --- | --- | --- |
| 热路径检查、键递增、`SENTINEL` 写 | 编译器发射（现状） | 必须内联；不依赖任何外部产物 |
| 运行时快路径中的极少数小函数（如需 `alwaysinline`） | 可选：`runtime/hot.ll`（LLVM 15 风格 IR）或升级后的运行时 bitcode + `link_in` | 只在这条窄路上付版本耦合的代价 |
| 分配器、回收、失败处理、全局对象 | C → `.o`/`.a` | 数据密集、需单测与 sanitizer；避免把分配器写进 IR，也避免 LTO 的版本要求 |

### 4.4 唯一真值

- `runtime/include/yian_rt.h` 定义 `YIAN_HDR_BYTES`、`YIAN_SLAB_BYTES`、`YIAN_SENTINEL`、类表等 ABI 常量。
- `compiler/codegen/cfg/lockmech.py` 保持 Python 镜像；新增一致性测试断言两侧常量相等（测试文件待定，见 §7 风险）。

## 5. 参数（本轮基准扫出的推荐值）

| 参数 | 推荐值 | 依据 |
| --- | --- | --- |
| 块头 | 16 B（`lock@0`、`active_size@8`） | 访问路径 1.46/8.64 ns（现状 2.09/10.62）；无对齐与侧数组约束 |
| slab | **64 KiB**，基址 64 KiB 对齐，基址前 8 B 放 slab 指针 | 吞吐拐点（16 K 31.9 → 64 K 27.4 ns/op 后走平）；归还 84.4%、稳态 5.9 MB；16 K 吞吐低 15%、归还低 12 个百分点；128 K 以上尾浪费 6.5×/12.3× 无收益 |
| 尺寸类 | **1.25× 等比**，16 B 起共 36 档（16…49152） | 取整浪费 1.17–1.54×（pow2 为 1.43–1.77×）；尾浪费绝对量有界（每类 ≈ 一个 slab，最坏 ~2.2 MB） |
| 类查找 | `clz` + 查表，O(1) | 线性扫描对细分表天然多 ~3 ns，与档位选择无关 |
| 最大类 | 块 ≤ 64 KiB 走 slab | 64/64 配置下 24 KiB、48 KiB churn 最快（21.7 / 19.5 ns/op）；压到 16 KiB 时 24 KiB churn 掉到 32.8 ns |
| 大对象 | 页取整的**精确尺寸 chunk + 按尺寸分桶缓存**，超水位才 `munmap` | 无缓存时 24 KiB churn 为 **1346 ns/op**（每对象一次 mmap+munmap）；有缓存为 19–33 ns |
| 空 slab 水位 | **1 MiB** 起步，建议比例化 `max(1 MiB, 25% × 映射量)` | 0–1 MiB 区间 ns/op 只差 1.2、RSS 5.0→5.9 MB、归还 85.6→84.4%；4 MiB 起用内存换速度（+3.2 MB 稳态换 1.4 ns/op） |
| 空闲链 | 每 slab 独立自由链（链写空闲块负载区），类内 slab 列表双向 O(1) 摘除 | 避免 O(空闲块数) 扫描 |

采用该参数后：混合尺寸长跑 2540 → **27.8 ns/op**，内部放大 4.3× → **1.09×**，增长-释放归还 0% → **84.4%**。

## 6. 分阶段实施

每阶段的硬判据：`tests/basic`、`tests/safety`、`tests/package` 在 fat 与 raw 两种模式下全绿。

### P0：运行时库的构建与链接骨架（语义零变化）

- 新增 `runtime/`（C 源码 + 头文件）与构建脚本，产出 `libyian_rt.o` / `libyian_rt.a`；先在库里实现**与现状等价**的运行时部分：`runtime_fail`、`panic`、键生成、argv/env/字面量/帧锁全局、wrapper `main`；池暂时保留在编译器里。
- `compiler/main.py::__link_object` 加上运行时库这一输入；`-t obj` 用 `clang -r` 合并为单个可重定位对象；`-t ll` 保持外部声明、不并入。
- 判据：三套件全绿；`-t ll` 输出可读且与现状等价；`-t obj`/`-t exe` 产物自包含（`nm -u` 无未定义运行时符号）；运行时库可用 `clang -fsanitize=address,undefined` 单独构建并通过自测。

### P1：分配器换实现（块头与检查不动）

- 在 C 库里实现尺寸类 arena（§5 参数）+ 大对象 chunk 缓存 + 水位回收；`yian_rt_alloc/release` 走新实现；删除 `get_pool_alloc/get_pool_release` 的手拼 IR。
- 块头仍是 32 B（`capacity`/`next` 变死字段），检查与 `lockmech.py` 不动。
- 判据：混合尺寸长跑 ≤ 45 ns/op、增长-释放归还 ≥ 70%、长跑 RSS 平稳；同尺寸周转不退化超过 1.5×。

### P2：块头 32 → 16 B

- `BlockHeader` 常量改为 16 B（`lock@0`、`active_size@8`），`data = lock_ptr + 16`；`is_raw`、`check_delete`、`check_view_access` 中的偏移随常量走；分配器的块大小与对齐跟着改。
- 判据：S=8/16 的 scan/rand 基准提升；三套件全绿；`--raw-pointers` 路径不受影响。

### P3：回收策略与调参

- 水位比例化或改为"空闲 slab 延迟衰减"（空闲超过阈值才回收），把水位从热路径移出；大对象缓存按尺寸分桶。
- 判据：长跑基准 RSS 有上界；`w5` 这类"增长-释放循环"的 ns/op 不劣于固定 1 MiB 水位。

### P4：收口

- 把本轮参数扫描的基准落成 `scripts/` 下的脚本（带阈值，改动后必跑）；`security.md` §6/§4.2 的"堆块头"描述改为"块头 + 槽"的新语义；`docs/compile_script.md` 补充运行时的链接说明。
- 判据：文档与实测一致；基准脚本一条命令产出报告。

## 7. 风险与开放问题

- **热路径退化**：任何把检查或 `active_size` 读取变成函数调用的实现都会丢掉 LLVM 的循环提升（实测 6.0× → 1.5× 即来自此）。边界必须按 §4.1 守住。
- **失败路径不得分配内存**：`yian_rt_fail`/`yian_rt_panic` 只能用静态消息 + `write(2, …)` + `_exit`；用 `printf`/`abort` 会引入分配与不可控行为。
- **确定性失败**：键耗尽（R003）、槽/arena 耗尽都必须显式终止而非回绕或退化。
- **跨目标**：运行时 bitcode 必须与目标三元组一致；YIAN 当前只面向宿主，按需在首次使用时用 clang 构建并缓存到 `build/`，并在文档里写明。
- **属性标注**：不随意启用 `malloc`/`noalias` 之类属性；返回块与元数据区的关系需要单独验证后再标。
- **`-t ll` 的自包含性**：选择不链接后，用户拿到的 `.ll` 需要外部运行时；文档必须给出完整的编译链接命令。
- **LLVM 版本耦合**：llvmlite（0.44）是 LLVM 15、系统 clang 是 18/20，实测无法把 clang 生成的 bitcode/opaque-pointer 文本 IR 并进 llvmlite 的模块。P-1 升级到 llvmlite ≥0.45（LLVM 20/22）后该阻塞消失，且实测 LLVM 22 能读 clang-18 的 bitcode 并 `link_in` 内联；但 **producer ≤ reader** 的约束长期存在（系统 clang 一旦新于 llvmlite 的 LLVM 就再次断裂），所以默认仍走 C 目标文件链接，bitcode 链接只作为窄用的可选能力，并在文档里写明版本对应关系。
- **单线程假设**：arena 与槽都不加锁；引入并发需要重新设计，本阶段只保证不把这条路堵死。
- **新可信边界**：运行时库成为安全关键组件（`security.md` §10 已列"运行时必须正确维护堆池、生命周期锁和生命周期键"），改由 C 实现后应当有独立单元测试与 sanitizer 运行；是否把这些测试纳入仓库需要确认（`AGENTS.md` 默认不新增测试）。

## 8. 参考

- 机制语义：[`docs/security.md`](../security.md) §4（锁与键）、§5（空间检查）、§6（堆对象）、§10（可信边界）
- 机制常量与谓词：`compiler/codegen/cfg/lockmech.py`
- 检查与发射：`compiler/codegen/llvm/builder.py`、`compiler/codegen/llvm/module.py`、`compiler/codegen/llvm/emit.py`
- 链接：`compiler/main.py::__link_object`；`llvmlite.binding.ModuleRef.link_in`
- 基准与参数依据：本轮 `bench_size*`（访问路径 × 尺寸）、`bench_alloc*`（分配器 × 长跑负载）、`bench_param*`（方案一参数扫描）

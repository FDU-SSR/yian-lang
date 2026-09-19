# 内存机制与运行时改造计划

## 1. 目标

1. **堆分配器**：从"单链首适配、不分割、不归还"换成**尺寸类 arena**，消除混合尺寸长跑下的病态行为与内存只涨不还。
2. **块头**：从 32 B 缩到 **16 B**（`lock@0`、`active_size@8`），提高小对象密度、缩短访问路径。
3. **运行时**：从"在编译器里手拼 IR"改成**独立编译的 C 运行时库**——C 源码是唯一真值，编译成 `.o`/`.a` 后由 clang 在链接步骤并入；热路径检查仍由编译器内联发射。

### 1.1 不做

- 不改胖指针表示（`T*` 40 B / `T[]` 32 B / `T&` 24 B / raw 8 B），不改检查清单与错误码。
- 不引入多线程：沿用 `docs/security.md` §10 的单线程假设。
- 不做 GC、并发标记、跨过程逃逸分析。
- 不改栈对象、字符串字面量、argv 三者的锁机制（仍是影子栈/全局锁槽）。

## 2. 现状与依据

### 2.1 现状构成

| 组成 | 位置 | 规模 |
| --- | --- | --- |
| 块头 | `compiler/codegen/cfg/lockmech.py::BlockHeader` | 32 B，`{lock, capacity, next, active_size}` @0/8/16/24 |
| 堆池 | `compiler/codegen/llvm/module.py::get_pool_alloc/get_pool_release` | 手拼 IR 约 130 行 |
| 运行时其余设施 | 同文件：`argc/argv` 全局、env 锁、字面量锁、键计数器、`runtime_fail`、`panic`、帧锁 arena、wrapper `main` | 手拼 IR 约 240 行 |
| 检查 | `compiler/codegen/llvm/builder.py` | 访问路径一次锁字 load + 寄存器比较；delete/view 另读 `active_size` |
| 发射与链接 | `compiler/codegen/llvm/emit.py`、`compiler/main.py::__link_exe` | llvmlite 解析 → pass pipeline → obj/asm/bc；`.o` 由 clang 链接 |

`module.py` 共 546 行，其中池约 130 行、其余运行时设施约 240 行，都是手拼 IR。

### 2.2 分配器对照（同一组负载，每格独立进程）

| 实现 | 同尺寸周转 | 混合尺寸长跑 | 长跑足迹 | 增长-释放归还 |
| --- | --- | --- | --- | --- |
| 单链首适配、不分割、不归还（现状） | **2.40 ns/op** | **2540 ns/op** | 58 → 252 MB（膨胀 4.3×） | **0%** |
| 尺寸类 arena（16 B 头） | 4.13 | 39.2 | 57.8 → 59.5 MB | **78%** |

现状的首适配平均每次扫过 **156 个空闲块**（每块一次 cache miss），是长跑性能问题的主因。该问题与指针布局无关，必须独立修掉。

### 2.3 访问路径对照（按对象尺寸）

| 负载 | 现状（32 B 头） | 16 B 头 | 8 B 头 | 锁槽独立 |
| --- | --- | --- | --- | --- |
| 8 B：scan / rand | 2.09 / 10.62 ns | 1.46 / 8.64 | **1.06 / 7.42** | 1.45 / 16.20 |
| 16 B：scan / rand | 1.11 / 9.32 | 0.74 / 7.39 | 0.74 / **7.24** | 0.75 / 12.68 |
| ≥256 B | 全部收敛（元数据占比 < 12%） | | | |

结论：锁字留在块内、与负载同区；64 B 对齐方案否决（小对象上明显更差）。

## 3. 决策

1. **采用 16 B 块头**：`lock@0`、`active_size@8`、`data = lock_ptr + 16`，块 16 B 对齐。`capacity` 与 `next` 删除——`capacity` 只是首适配的复用判据（尺寸类隐含容量），`next` 移到空闲块的负载区。检查侧只依赖 `active_size`（`check_delete` 与视图校验比的是逻辑载荷长度），不受影响；空闲块的负载被链指针占用后，悬垂指针连旧负载也读不到，`docs/security.md` §6 的描述随之更新。
2. **分配器用尺寸类 arena**，参数见 §5。
3. **运行时做成独立库**：C 源码 → `.o`/`.a` → 由 clang 在链接步骤并入；不把运行时 IR 并进 LLVM 模块，也不依赖 bitcode 跨版本链接。
4. **锁语义全部留在编译器侧**：键生成（堆键/栈键递增）、锁槽写入、`SENTINEL` 失效、`live` 判定都不进运行时库；运行时只负责内存，并把键计数器、帧锁 arena/深度等对象以**外部全局符号**提供给内联代码读写。

## 4. 运行时边界与 ABI

### 4.1 留在编译器内联发射

| 项 | 原因 |
| --- | --- |
| 访问路径检查（`live` 的 load + 比较 + 分支、边界比较） | 必须能被 LLVM 内联与提升 |
| `__load_active_size` 的守卫加载 | delete/view 冷路径，但需要与检查同一基本块形状 |
| 帧锁进入/退出（push + re-key、每条 return 前写 `SENTINEL` + pop） | 分布在所有返回路径上，且只是几次存储 |
| 键计数器递增、胖指针构造/字段提取、`is_raw` 判定 | 纯值操作或单字读写，无调用 |

### 4.2 移入运行时库

| 项 | 说明 |
| --- | --- |
| `__yian_runtime_fail(const uint8_t *message, uint64_t length)` | 原样写 stderr 后 `_exit(1)`；`noreturn` + `cold`，不分配内存。消息文本由编译器决定 |
| `__yian_panic(const uint8_t *message, uint64_t length)` | 写 `yian: panic: ` + 消息 + 换行后 `_exit(1)` |
| `main(int argc, char **argv)`（wrapper） | 校验参数 ABI（`argc >= 0`、`argv != null`），写入参数全局，调用 `__yian_main()`，返回 0 |
| `__yian_argc` / `__yian_argv` | 进程参数全局（wrapper 写，`@argc`/`@arg_bytes` 读） |
| `__yian_lit_lock`、`__yian_env_lock`、`__yian_key_heap`、`__yian_key_stack` | 锁槽与键计数器；由内联代码直接 load/store |
| `__secl_frame_locks[]`（2^20 × u64）、`__secl_frame_lock_depth` | 帧锁影子栈（BSS，页按首次触达常驻） |
| P1 起：`__secl_pool_alloc` / `__secl_pool_release` 的新实现 | 尺寸类 arena（P1 换实现，块头先不动） |

### 4.3 交付形式与链接

运行时按需首次构建、缓存到 `build/`，目标三元组固定为宿主 `x86_64-unknown-linux-gnu`。

| 目标 | 链接方式 |
| --- | --- |
| `-t exe` | `clang user.o libyian_rt.a -o exe`（`compiler/main.py::__link_exe` 的链接命令加一个输入） |
| `-t obj` | `clang -r user.o runtime.o -o out.o`，保持单文件自包含 |
| `-t ll` / `-t bc` / `-t asm` | 运行时不并入，符号以外部声明/未定义符号出现；文档给出完整编译链接命令 |

### 4.4 唯一真值

- `runtime/include/yian_rt.h` 定义 ABI 常量与全局对象声明：`YIAN_FRAME_LOCK_SLOTS`、`YIAN_FRAME_LOCK_SLOT_BYTES`、`YIAN_LITERAL_KEY`、`YIAN_ABI_FAIL_MESSAGE`、堆块头大小（P2 起）。
- `compiler/codegen/cfg/lockmech.py` 是这些常量的 Python 镜像；`runtime/build.py --check` 断言两侧相等，并断言 `YIAN_ABI_FAIL_MESSAGE` 与 S002 消息逐字节一致。
- 失败诊断的消息表只有一份：留在编译器侧（`compiler/runtime_error.py`），运行时只接收 `(指针, 长度)` 并原样写出，因此两侧都不会有第二份消息。
- `docs/security.md` §4/§5/§6/§10 的块头与池描述按新块头、新分配器更新。

## 5. 参数

| 参数 | 取值 | 依据 |
| --- | --- | --- |
| 块头 | 16 B（`lock@0`、`active_size@8`） | 访问路径 1.46/8.64 ns（现状 2.09/10.62）；无对齐与侧数组约束 |
| slab | **64 KiB**，基址 64 KiB 对齐，基址前 8 B 放 slab 指针 | 吞吐拐点（16 K 31.9 → 64 K 27.4 ns/op 后走平）；归还 84.4%、稳态 5.9 MB；16 K 吞吐低 15%、归还低 12 个百分点；128 K 以上尾浪费 6.5×/12.3× 无收益 |
| 尺寸类 | **1.25× 等比**，16 B 起共 36 档（16…49152） | 取整浪费 1.17–1.54×（pow2 为 1.43–1.77×）；尾浪费绝对量有界（每类 ≈ 一个 slab，最坏 ~2.2 MB） |
| 类查找 | `clz` + 查表，O(1) | 线性扫描对细分表天然多 ~3 ns，与档位选择无关 |
| 最大类 | 块 ≤ 64 KiB 走 slab | 64/64 配置下 24 KiB、48 KiB churn 最快（21.7 / 19.5 ns/op）；压到 16 KiB 时 24 KiB churn 掉到 32.8 ns |
| 大对象 | 页取整的**精确尺寸 chunk + 按尺寸分桶缓存**，超水位才 `munmap` | 无缓存时 24 KiB churn 为 1346 ns/op（每对象一次 mmap+munmap）；有缓存为 19–33 ns |
| 空 slab 水位 | **1 MiB** 起步，可选比例化 `max(1 MiB, 25% × 映射量)` | 0–1 MiB 区间 ns/op 只差 1.2、RSS 5.0→5.9 MB、归还 85.6→84.4%；4 MiB 起用内存换速度（+3.2 MB 稳态换 1.4 ns/op） |
| 空闲链 | 每 slab 独立自由链（链写空闲块负载区），类内 slab 列表双向 O(1) 摘除 | 避免 O(空闲块数) 扫描 |

采用该参数后：混合尺寸长跑 2540 → **27.8 ns/op**，内部放大 4.3× → **1.09×**，增长-释放归还 0% → **84.4%**。

## 6. 分阶段实施

每阶段的硬判据：`tests/basic`、`tests/safety`、`tests/package` 在 fat 与 raw 两种模式下全绿。

### P0 运行时库的构建与链接骨架（语义零变化）

- 新增 `runtime/`：`include/yian_rt.h`（ABI 常量与声明）、`src/runtime.c`（全局对象、失败路径、wrapper `main`）、`build.py`（构建 + 一致性断言 + 自测）、`selftest/selftest.c`。产物 `build/runtime/libyian_rt.{o,a}`，由 `compiler/runtime_lib.py` 按需构建并缓存（源码比产物新才重建）。
- 编译器侧把对应全局与函数改成**外部声明**（池暂时留在编译器），不再发射 wrapper `main`。
- `compiler/main.py::__link_exe` 链接时加入静态库；`-t obj` 用 `clang -r -nostdlib` 把它与用户对象合并为单个可重定位对象；`-t ll`/`bc`/`asm` 保持外部声明、不并入。
- 自测放在 `runtime/`：`python3 runtime/build.py --check`（一致性断言 + 普通自测）与 `--asan`（ASan/UBSan 自测），不纳入 `tests/`。
- 判据：三套件全绿；`-t exe`/`-t obj` 产物自包含（`nm -u` 无未定义运行时符号）；`-t ll`/`bc`/`asm` 里运行时符号为外部声明，按文档命令可与运行时库一起链接运行；运行时库可 ASan/UBSan 构建并通过自测。

### P1 分配器换实现（块头与检查不动）

- 在 C 库里实现尺寸类 arena（§5 参数）+ 大对象 chunk 缓存 + 水位回收；`yian_rt_alloc/release` 走新实现；删除 `get_pool_alloc`/`get_pool_release` 的手拼 IR。
- 块头仍是 32 B（`capacity`/`next` 变死字段），`lockmech.py` 与检查不动，空闲链暂用块头 `next`。
- 交付 `scripts/` 下的分配器基准（单尺寸 churn、混合尺寸 churn、稳态 RSS，两种模式），判据数据由它产出。
- 判据：混合尺寸长跑 ≤45 ns/op、增长-释放归还 ≥70%、长跑 RSS 平稳；同尺寸周转不退化超过 1.5×。

### P2 块头 32 → 16 B

- `BlockHeader` 改为 16 B（`lock@0`、`active_size@8`），`data = lock_ptr + 16`；`is_anchor`、`check_delete`、`check_view_access` 中的偏移随常量走；空闲链改为写在空闲块负载区；C 侧 `YIAN_HDR_BYTES` 同步。
- `docs/security.md` §6 的块头与池描述同步。
- 判据：S=8/16 的访问路径基准提升；三套件全绿；`--raw-pointers` 路径不受影响。

### P3 回收策略与调参（可选）

- 水位比例化或改为"空闲 slab 延迟衰减"（空闲超过阈值才回收），把水位判断移出热路径；大对象缓存按尺寸分桶。
- 判据：长跑基准 RSS 有上界；"增长-释放循环"的 ns/op 不劣于固定 1 MiB 水位。

### P4 收口

- `docs/compile_script.md` 补运行时的构建与链接说明；`AGENTS.md` 的 CLI 说明加入运行时库这一输入；`scripts/` 的基准带阈值，改动后必跑。
- 判据：文档与实测一致；基准脚本一条命令产出报告。

## 7. 风险与开放问题

- **热路径退化**：把检查或 `active_size` 读取变成函数调用会丢掉 LLVM 的循环提升，`-O2` 下的大部分收益来自这种提升。边界必须按 §4.1 守住。
- **失败路径不得分配内存**：`yian_rt_fail`/`yian_rt_panic` 只能用静态消息 + `write(2, …)` + `_exit`；用 `printf`/`abort` 会引入分配与不可控行为。
- **确定性失败**：键耗尽（R003）、槽/arena 耗尽都必须显式终止，不回绕、不退化。
- **属性标注**：不随意启用 `malloc`/`noalias` 之类属性；返回块与元数据区的关系需要验证后再标。
- **`-t ll`/`bc`/`asm` 的自包含性**：运行时是外部依赖，文档必须给出完整的构建与链接命令。
- **单线程假设**：arena 与槽都不加锁；引入并发需要重新设计，本阶段只保证不把这条路堵死。
- **新可信边界**：运行时库是安全关键组件（`security.md` §10 已把堆池、生命周期锁与键列为运行时职责），改由 C 实现后必须有独立自测与 sanitizer 运行，自测放 `runtime/`。

## 8. 参考

- 机制语义：`docs/security.md` §4（锁与键）、§5（空间检查）、§6（堆对象）、§10（可信边界）
- 机制常量与谓词：`compiler/codegen/cfg/lockmech.py`
- 检查与发射：`compiler/codegen/llvm/builder.py`、`compiler/codegen/llvm/module.py`、`compiler/codegen/llvm/emit.py`
- 运行时：`runtime/include/yian_rt.h`、`runtime/src/runtime.c`、`runtime/build.py`、`runtime/selftest/selftest.c`、`compiler/runtime_lib.py`
- 链接：`compiler/main.py::__link_exe`、`__merge_runtime_object`

# C → YIAN 翻译改写与复核

日期：2026-10-28。判据仅见 [C → YIAN 规范](c-to-yian.md) v0.2。
本报告审查仓库 C 程序到 YIAN 的改写，不审查上游漏洞到 C 模型。

已对全部 60 对应用可落实的明确条款并复核源码：38 个 CVE、22 个基准
（AWFY 14、BG 5、fatptr 3）。目前确认剩余 **14 项，涉及 13 对**：
12 项存储期差异、1 项状态与部分输出的规则边界、1 项实验依赖的调用边界。
其中性能基准剩余 **5 项 / 5 对**；其余 17 个基准未发现额外待处理项。
“未发现”是当前静态审查结论，不是全输入等价证明或全部运行通过。

这些计数按待处理问题计，不把已允许的引用/切片、bool、容器和库函数表示差异重复算作违规。
同一 HyperKit 配对有两个问题，因此问题数与配对数不同。

## 已完成的改写

- 单对象改用引用，连续缓冲改用切片，可空对象/区域用 Option；实际游标运算保留一般指针。
  基准的动态切片由分配加全范围视图形成，兼容 raw/fat，不依赖 raw 不支持的隐式转换。
- 源码中 849 处索引/范围表达式已作结构检查：变量表达式在使用处转换为 u64，字面量显式为 u64；
  一般指针整数偏移也在使用处转换。循环下标为 u64，递减循环避免零后的无符号回绕。
- CD、DeltaBlue、Havlak、JSON、Richards 的 150 个业务方法已还原为普通函数，全部调用者同步；
  标准库 Vec/String 等方法保留。DeltaBlue 的数值 strength 枚举使用 i32 const，身份变体仍用 enum。
- 14 个 AWFY 与三个 fatptr 程序读取 argv 规模，原数值保留为默认值和 bench-scale 标记。
  C atoi/atoll 所需十进制前缀、空白、符号处理由普通辅助函数提供；参数字符串和容器在计算前释放。
- 修复 fann 无花括号 for 的循环体；count[i]=0 在循环后。Mandelbrot 恢复直接位移。
  nbody 的 bodies[5] 与 storage 的 Random 恢复栈存储；revcomp 使用与 C 相同的 387 字节栈输入、400 字节表。
  C 输入恰好填满 387 字节，末尾没有 NUL；扫描 end=386 的原边界保留。
- BG 五项恢复 C 输出；fann 保留退出码 51。三态与 ASan 运行器使用同一 spec 的输出/退出结果，
  不再把正常结果固定为退出 0；测量次数、采样、统计和时间比值公式未改动。
- radare2 的调用者恢复 free_object，首次非法操作对应 data 字段读取；Tremor 改用 Vec<Entry> 并释放被替换值。
  ScprStream 为栈值、ExecArea 为堆对象、Softc 为栈值且 iovec 内嵌；原固定字段数组保留。
- Sudo/OpenSC/RPC/NamedData/RFX/GIF/bitmap 分配的状态输出结构按使用方式返回 Option；NULL 分支、BGP 零长度分支和必要错误退出已恢复。
  普通尺寸、错误计数、返回值按 C 宽度恢复，逻辑标志和循环下标采用已允许的类型。
- C 的五处模块可变状态（tx、svc、permute、storage、fasta）改为调用者持有并显式传参。
  为保留 C 原有静态存储期，C 侧使用局部 static；YIAN 侧的存储期差异仍计入下表。

## 剩余存储期差异：12 项

| ID | 配对 | 当前差异 | 证据 |
| --- | --- | --- | --- |
| R01 | CVE-2017-9995 | 静态 packet → YIAN 堆缓冲 | [C](../cve/CVE-2017-9995/c/src/reproducer.c) / [YIAN](../cve/CVE-2017-9995/yian/src/scenario.an) |
| R02 | CVE-2020-15999 | 静态 PNG 输入 → YIAN 堆缓冲 | [C](../cve/CVE-2020-15999/c/src/png_input.c) / [YIAN](../cve/CVE-2020-15999/yian/src/png.an) |
| R03 | CVE-2021-32845 | 静态 guest_input → YIAN 栈数组 | [C](../cve/CVE-2021-32845/c/src/reproducer.c) / [YIAN](../cve/CVE-2021-32845/yian/src/scenario.an) |
| R04 | CVE-2022-3806 | C 调用者 static tx → YIAN run 自动对象 | [C](../cve/CVE-2022-3806/c/src/reproducer.c) / [YIAN](../cve/CVE-2022-3806/yian/src/scenario.an) |
| R05 | CVE-2023-36273 | 静态 u16 CRC 表 → YIAN 函数自动数组 | [C](../cve/CVE-2023-36273/c/src/reproducer.c) / [YIAN](../cve/CVE-2023-36273/yian/src/support.an) |
| R06 | CVE-2023-39351 | 静态 RFX 字节输入 → YIAN 堆缓冲 | [C](../cve/CVE-2023-39351/c/src/reproducer.c) / [YIAN](../cve/CVE-2023-39351/yian/src/scenario.an) |
| R07 | CVE-2023-4264 | C 调用者 static svc_insts[4] → YIAN run 自动数组 | [C](../cve/CVE-2023-4264/c/src/reproducer.c) / [YIAN](../cve/CVE-2023-4264/yian/src/scenario.an) |
| R08 | CVE-2023-45664 | 静态 GIF 输入 → YIAN 栈数组 | [C](../cve/CVE-2023-45664/c/src/reproducer.c) / [YIAN](../cve/CVE-2023-45664/yian/src/scenario.an) |
| R09 | AWFY/permute | C benchmark 的 static count → YIAN 每次调用的自动 count | [C](../bench/AWFY/c/permute.c) / [YIAN](../bench/AWFY/an/permute.an) |
| R10 | AWFY/storage | C benchmark 的 static count_arrays → YIAN 自动 count；Random 已同为栈对象 | [C](../bench/AWFY/c/storage.c) / [YIAN](../bench/AWFY/an/storage.an) |
| R11 | BG/fasta | C main 的 static 概率表、seed、checksum → YIAN main 自动状态 | [C](../bench/BG/c/fasta.c) / [YIAN](../bench/BG/an/fasta.an) |
| R12 | BG/spectralnorm | C 三个 static 数组 → YIAN 三个堆缓冲 | [C](../bench/BG/c/spectralnorm.c) / [YIAN](../bench/BG/an/spectralnorm.an) |

这些项不能以“固定输入可运行”替代存储期对应。当前 YIAN const 表达标量与 str，不能直接声明
上述静态聚合数组或可变静态状态。ASCII 静态输入已在 tinyproxy/RPC 中用 const str 保留；
含任意字节的 PNG/GIF/RFX 等不能直接把大于 127 的字节写成 Unicode 字符串并声称字节相同。
本次没有修改编译器以增加静态聚合/状态支持，也没有通过改短 C 的存储期消除表面差异。

## R13：HyperKit 的状态与部分输出

[virtio_vq_getchain](../cve/CVE-2021-32845/yian/src/support.an) 仍返回计数/状态，
通过 pidx 和 iov 引用写回输出。C 在确认 descriptor head 之前已经写入 pidx 并推进 last_avail；
部分失败还会保留已写 iovec。本案例的通知函数忽略失败状态，继续使用前一次的 iovec，漏洞依赖该行为。
简单改成“None 时不取任何输出”会丢失这些副作用，改成只在 Some 时继续则会消除目标访问。
需要确定 G7 对这类失败前可见回写的具体形式；当前保留原行为并将它列为未闭环项。

## R14：call_abi 的真实调用边界

[C](../bench/fatptr/c/call_abi.c) 使用 noinline；[YIAN](../bench/fatptr/an/call_abi.an)
仍有 mix_in / mix_wide 两个普通函数，但当前 native -O2 会将它们内联。
G1 改写后两函数的数组参数均为切片：当前 checked 为 24 字节、raw 为 16 字节。
数学结果和 argv 参数相同，不足以证明这个实验仍在测量真实函数调用成本。
源码与 spec note 已删除“三态都不内联”的断言；该项不属于论文 paper 集合。

检查的是优化后的 bitcode：分别对本文件以 -O2 -t bc 编译 raw/fat，再用 llvm-dis-22 读取。
两态中原 mix 函数的定义与调用（未优化 IR 中的 yian.fn.387/388）均消失。
-t ll 即使带 -O2 也输出优化前 IR，不能据其中仍有 call 推断实际调用保留；
这一行为可在 [backend.emit](../yianc/src/driver/backend.an) 的 Ll/Bc 分支核对。
本任务未改变调用协议或添加编译器属性，当前 call_abi 不应作为三态调用成本证据。

## 已允许的实现差异

以下仍然存在，但按用户已确认的 G3/G5/G6/G8/G10 不计入上面的 14 项：

- libc 辅助函数只覆盖实际输入需求；不补全 scanf/getopt/strcmp 的所有接口与内部扫描过程。
- Vec/String 的容量、增长、内部布局和分配次数可不同；Tremor、CD、Havlak 等保留需要的元素和别名关系。
- 逻辑 int 可用 bool，循环下标用 u64；普通数据字段与原窄化位置另外核对。
- realloc 可显式搬迁，清理可改变内部遍历顺序；Mbed TLS 的递归逆序释放未据此单独判违规。
- packed 按字节解析、union 按 enum 表示；实验依赖的 noinline 作用仍须保留，所以 R14 继续计入。

## 逐对结果

“无新增项”表示未发现上述问题以外的额外待处理差异，不能解释为已运行或已证明等价。
所有 22 个基准共用同一份 .an 编译 raw/fat，没有独立 .raw.an 覆盖；ALLOC 四项没有独立 C 参考，不计入。

### 基准：22 对

| 配对 | 论文 paper 集合 | 剩余项 | 已核对的主要改写 |
| --- | --- | --- | --- |
| [AWFY/bounce](../bench/AWFY/an/bounce.an) | 是 | 无新增项 | Random/Ball 引用、结果 i32、Ball 切片、argv |
| [AWFY/cd](../bench/AWFY/an/cd.an) | 否 | 无新增项 | 普通函数、树值引用、u64 下标、argv |
| [AWFY/deltablue](../bench/AWFY/an/deltablue.an) | 否 | 无新增项 | 普通函数、i32 strength const/idx、递减排序游标、argv |
| [AWFY/havlak](../bench/AWFY/an/havlak.an) | 否 | 无新增项 | 普通函数、u64 遍历和倒序边界、argv |
| [AWFY/json](../bench/AWFY/an/json.an) | 否 | 无新增项 | Parser 普通函数、DOC 字节、容器、argv |
| [AWFY/list](../bench/AWFY/an/list.an) | 是 | 无新增项 | Option 引用、递归和释放、argv |
| [AWFY/mand](../bench/AWFY/an/mand.an) | 是 | 无新增项 | 直接位移、u64 下标、argv |
| [AWFY/nbody](../bench/AWFY/an/nbody.an) | 是 | 无新增项 | 固定栈数组、浮点运算顺序、argv |
| [AWFY/permute](../bench/AWFY/an/permute.an) | 是 | R09 | 交换的显式指针运算、递减退出、argv |
| [AWFY/queen](../bench/AWFY/an/queen.an) | 是 | 无新增项 | bool 标志切片、回溯索引/恢复、argv |
| [AWFY/richards](../bench/AWFY/an/richards.an) | 否 | 无新增项 | 普通函数、bool 状态、enum 分派、argv |
| [AWFY/sieve](../bench/AWFY/an/sieve.an) | 是 | 无新增项 | bool 标记、u64 下标、argv |
| [AWFY/storage](../bench/AWFY/an/storage.an) | 是 | R10 | 栈 Random、i32 计数、引用递归、argv |
| [AWFY/towers](../bench/AWFY/an/towers.an) | 是 | 无新增项 | 引用链、递减退出、argv |
| [BG/binarytree](../bench/BG/an/binarytree.an) | 是 | 无新增项 | 结构体局部复制、res i32、深度/计数 u32、C 输出 |
| [BG/fann](../bench/BG/an/fann.an) | 是 | 无新增项 | for 语句范围、u64 下标、C 输出和退出 51 |
| [BG/fasta](../bench/BG/an/fasta.an) | 是 | R11 | const、显式状态参数、i32 数量、C 输出 |
| [BG/revcomp](../bench/BG/an/revcomp.an) | 是 | 无新增项 | 同字节栈输入、400 字节表、左右边界、C 输出 |
| [BG/spectralnorm](../bench/BG/an/spectralnorm.an) | 是 | R12 | i32 数量、原 v 零初始化、C 输出 |
| [fatptr/call_abi](../bench/fatptr/an/call_abi.an) | 否 | R14 | 切片参数、运行时 CALLS 和 argc、优化后内联 |
| [fatptr/chase](../bench/fatptr/an/chase.an) | 否 | 无新增项 | Option<Node&>、i64 规模/轮数、argv |
| [fatptr/copy_struct](../bench/fatptr/an/copy_struct.an) | 否 | 无新增项 | 引用加切片字段、i64 数量/轮数、浅复制、argv |

### CVE：38 对

| 配对 | 剩余项 | 当前记录 |
| --- | --- | --- |
| [CVE-2001-1009](../cve/CVE-2001-1009/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2003-0721](../cve/CVE-2003-0721/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2005-0102](../cve/CVE-2005-0102/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2016-5180](../cve/CVE-2016-5180/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2017-9995](../cve/CVE-2017-9995/README.md) | R01 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2018-11410](../cve/CVE-2018-11410/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2020-15999](../cve/CVE-2020-15999/README.md) | R02 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2021-3156](../cve/CVE-2021-3156/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2021-32495](../cve/CVE-2021-32495/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2021-32845](../cve/CVE-2021-32845/README.md) | R03,R13 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2021-33304](../cve/CVE-2021-33304/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2021-37778](../cve/CVE-2021-37778/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2021-39228](../cve/CVE-2021-39228/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2022-0523](../cve/CVE-2022-0523/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2022-31003](../cve/CVE-2022-31003/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2022-3806](../cve/CVE-2022-3806/README.md) | R04 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-0341](../cve/CVE-2023-0341/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-1452](../cve/CVE-2023-1452/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-23086](../cve/CVE-2023-23086/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-26463](../cve/CVE-2023-26463/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-2905](../cve/CVE-2023-2905/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-2977](../cve/CVE-2023-2977/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-36273](../cve/CVE-2023-36273/README.md) | R05 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-39351](../cve/CVE-2023-39351/README.md) | R06 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-41361](../cve/CVE-2023-41361/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-4264](../cve/CVE-2023-4264/README.md) | R07 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-45664](../cve/CVE-2023-45664/README.md) | R08 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-49287](../cve/CVE-2023-49287/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-49606](../cve/CVE-2023-49606/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2024-23310](../cve/CVE-2024-23310/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2024-37407](../cve/CVE-2024-37407/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2024-42478](../cve/CVE-2024-42478/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2024-45508](../cve/CVE-2024-45508/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2024-47538](../cve/CVE-2024-47538/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2024-47777](../cve/CVE-2024-47777/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2025-47917](../cve/CVE-2025-47917/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2026-26399](../cve/CVE-2026-26399/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2026-45959](../cve/CVE-2026-45959/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |

## 局部证据与未执行范围

- 静态检查覆盖 60 对的 147 个 .an 文件：均可解析；849 处索引/范围表达式、全部项目内函数/构造调用参数数目与
  custom impl 遗留检查无错误。此项不是 native 类型检查，也不能替代运行时等价验证。
- native 最小引用/切片/参数释放探针运行输出 representation pass；queen 用 argv=1 在 raw/fat 编译并运行，均退出 0。
  call_abi 用 argv=1 的 O0 运行输出 call_abi pass；其 O2 调用边界检查另按 R14 记录，未把 O0 结果当成 O2 实验验收。
- bounce 用 argv=1 编译运行，1331 次反弹断言通过、退出 0。17 个命令行解析辅助函数的签名和函数体一致；
  源码级 C/YIAN 单对象参数比对补齐 Random/Ball 的引用传递。
- CD、Havlak、DeltaBlue、JSON、Richards、binarytree、storage 进行精确 native --check；DeltaBlue 的 enum 绑定引用参数问题已修复并重查通过。
- CVE-2017-9995、CVE-2021-39228、CVE-2021-3156、CVE-2023-2977、CVE-2024-42478、CVE-2020-15999 只进行了精确 YIAN build-only，均通过。
  没有将 BUILD_ONLY 写成案例执行通过。
- CVE-2021-32495 精确 --language both 运行通过：C ASan heap-use-after-free，READ of size 8，位置 free_object 的 data 字段；
  YIAN 退出 1，S003 dangling reference access。项目哈希（项目内全部文件、排除 build）：
  C `c8a2f280dcbd50aa75775e15ac06924618e3b114b47a057aa5ca11ab7f57d8cb`；
  YIAN `f86a90538bcc544d6f2816f2c4f8221caf5ad2f38fb4b41132200bb4d292ee54`。
- RFX 的 Option 改写进行了精确构建和单侧执行：CVE-2023-39351 退出 1，S002 invalid memory access，
  对应零容量 tile 数组的首次索引访问，与 case.json 一致。
- GIF 的截断帧 Option 改写进行了精确构建和单侧执行：CVE-2023-45664 退出 1，S006 invalid deallocation；
  截断帧没有因 Option 失败分支而被跳过，原双重释放路径仍触发。
- 三态运行器的单案例替身探针调用 measure_triple：结果 51 / 输出 51 被接受，共同错误输出或错误退出码被拒绝；
  该探针不运行基准二进制、不产生测量结果。
- C 五个状态重组文件通过 clang -fsyntax-only；两个 Python 运行器通过 py_compile；源码/文档使用 git diff --check。
- yianc/anx 的组件源码及二进制 SHA-256 与本机安装记录一致；没有重建工具链。
- 未运行全部 38 个 CVE、全部 22 个基准、编译器整组测试或性能重测。
  旧 results/ 与论文样本保持为历史测量，不能作为当前改写源码的性能结果。

## 源码身份

本轮改写基于子模块 ad603b0fb206a0280f5a73f2720c4dfef180be9c。
源码集合 323 文件：38 个 C 项目的 .c/.h（排除 build）、38 个 YIAN src 的 .an，以及 22 对基准 .c/.an。
按相对路径排序，逐项拼接“路径 UTF-8 + NUL + 文件 SHA-256 的 32 字节摘要”，再求 SHA-256：

- 改写前：ae66a8f953ec1dcefc0e8943fe1fc34a9ad5c00faf17a413685e9ae9c8dca0d6
- fcd6be82 提交源码（不含下列注释更正）：e473abe13fe8b11aeeb5c3cb8de5a53adcc94831b467a66cacca31f998c5e9d0
- 当前源码（含注释更正，与审查时工作区一致）：37c9c5995ab5e2811c02ae1ebf3de6098a3a8f2f2aeeef89c878c28dde7a799a

两项摘要仅因以下六个 C 文件的注释更正而不同；更正已另行提交，不改变可执行代码：
- `cve/CVE-2001-1009/c/src/reproducer.c`
- `cve/CVE-2003-0721/c/src/reproducer.c`
- `cve/CVE-2005-0102/c/src/reproducer.c`
- `cve/CVE-2024-23310/c/src/reproducer.c`
- `cve/CVE-2024-42478/c/src/tensor.c`
- `cve/CVE-2026-45959/c/src/reproducer.c`


哈希用于确定被审查的源码，不证明语义等价。案例 README 与本报告记录应用和证据，规则仍只有 c-to-yian.md 一份。

# C → YIAN 翻译改写与复核

日期：2026-10-29。判据仅见 [C → YIAN 规范](c-to-yian.md) v0.3。
本报告审查仓库 C 程序到 YIAN 的改写，不审查上游漏洞到 C 模型。

已对全部 60 对应用可落实的明确条款并复核源码：38 个 CVE、22 个基准
（AWFY 14、BG 5、fatptr 3）。目前确认剩余 **8 项，涉及 7 对**：
6 项只读静态对象的存储期差异、1 项状态与部分输出的规则边界、1 项实验依赖的调用边界。
其中性能基准剩余 **1 项 / 1 对**；其余 21 个基准未发现额外待处理项。
“未发现”是当前静态审查结论，不是全输入等价证明或全部运行通过。

这些计数按待处理问题计，不把已允许的引用/切片、bool、容器和库函数表示差异重复算作违规。
同一 HyperKit 配对有两个问题，因此问题数与配对数不同。

## 已完成的改写

- 单对象改用引用，连续缓冲改用切片，可空对象/区域用 Option；实际游标运算保留一般指针。
  基准的动态切片由分配加全范围视图形成，兼容 raw/fat，不依赖 raw 不支持的隐式转换。
- 源码中 873 处索引/范围表达式已作结构检查：变量表达式在使用处转换为 u64，字面量显式为 u64；
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
- C 的静态可变对象按 v0.3 用入口持有的堆对象表示，通过引用或切片共享；
  重复基准调用只进行 C 原有的计数器复位，不重新分配静态状态。自动对象和只读常量分别保持原处理。

## 静态可变对象：14 个对象 / 8 对均已有堆表示

| 配对 | C 静态可变对象 | 当前 YIAN 分配与共享 |
| --- | --- | --- |
| [AWFY/cd](../bench/AWFY/an/cd.an) | recurse.marker、handle_new_frame.marker | main 分配两个独立 u8&，跨工作单元、帧和递归共享，树值存其引用 |
| [AWFY/permute](../bench/AWFY/an/permute.an) | count | main 分配 i32&，benchmark 每次仅复位为 0 |
| [AWFY/storage](../bench/AWFY/an/storage.an) | count_arrays | main 分配 i32&，benchmark 复位后传入递归；Random 保持栈对象 |
| [BG/fasta](../bench/BG/an/fasta.an) | g_seed、checksum、iub、homosapiens | main 分配 i32&、i64& 和两个 AminoAcid[]，原概率、初值及更新顺序保留 |
| [BG/spectralnorm](../bench/BG/an/spectralnorm.an) | u、v、tmp | 原有三个 main 堆数组已经符合规则，本轮不改源码 |
| [CVE-2022-3806](../cve/CVE-2022-3806/yian/src/scenario.an) | tx | run 分配一个 tx_global_t&，保持 buf 的别名及错误释放路径 |
| [CVE-2023-4264](../cve/CVE-2023-4264/yian/src/scenario.an) | svc_insts[4] | run 分配一个四元素堆切片；uri 仍内嵌，friendly_name/gtbs_inst 仍为自动对象 |
| [CVE-2023-49606](../cve/CVE-2023-49606/yian/src/scenario.an) | headers[] 的可变指针槽 | run 分配 str[] 表并传入遍历；所指字符串仍为只读字面量 |

七对进行了源码改写，spectralnorm 三个数组复核后沿用。分配在覆盖全部使用者的入口，
普通退出时在最后使用后清理；CVE 若按预期 guard 终止，不执行终止点之后的清理。
R04、R07、R09、R10、R11、R12 因新规则已关闭，保留编号以对应上轮报告。
C 的声明与程序、case.json、命令行及预期输出均未改变；本轮不涉及只读 static const 对象。

## 剩余只读静态对象的存储期差异：6 项

| ID | 配对 | 当前差异 | 证据 |
| --- | --- | --- | --- |
| R01 | CVE-2017-9995 | 只读静态 packet → YIAN 堆缓冲 | [C](../cve/CVE-2017-9995/c/src/reproducer.c) / [YIAN](../cve/CVE-2017-9995/yian/src/scenario.an) |
| R02 | CVE-2020-15999 | 只读静态 PNG 输入 → YIAN 堆缓冲 | [C](../cve/CVE-2020-15999/c/src/png_input.c) / [YIAN](../cve/CVE-2020-15999/yian/src/png.an) |
| R03 | CVE-2021-32845 | 只读静态 guest_input → YIAN 栈数组 | [C](../cve/CVE-2021-32845/c/src/reproducer.c) / [YIAN](../cve/CVE-2021-32845/yian/src/scenario.an) |
| R05 | CVE-2023-36273 | 只读静态 u16 CRC 表 → YIAN 函数自动数组 | [C](../cve/CVE-2023-36273/c/src/reproducer.c) / [YIAN](../cve/CVE-2023-36273/yian/src/support.an) |
| R06 | CVE-2023-39351 | 只读静态 RFX 字节输入 → YIAN 堆缓冲 | [C](../cve/CVE-2023-39351/c/src/reproducer.c) / [YIAN](../cve/CVE-2023-39351/yian/src/scenario.an) |
| R08 | CVE-2023-45664 | 只读静态 GIF 输入 → YIAN 栈数组 | [C](../cve/CVE-2023-45664/c/src/reproducer.c) / [YIAN](../cve/CVE-2023-45664/yian/src/scenario.an) |

这些项的 C 对象均为 const，不在本轮静态可变对象规则范围内。当前 YIAN const 表达标量与 str，
不能直接声明上述只读静态聚合数组。不能以“固定输入可运行”替代仍要求的存储期对应。ASCII 静态输入已在 tinyproxy/RPC 中用 const str 保留；
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

以下仍然存在，但按用户已确认的条款（含 v0.3 静态可变对象规则）不计入上面的 8 项：

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
| [AWFY/cd](../bench/AWFY/an/cd.an) | 否 | 无新增项 | 普通函数、两个入口堆标记、树值引用、argv |
| [AWFY/deltablue](../bench/AWFY/an/deltablue.an) | 否 | 无新增项 | 普通函数、i32 strength const/idx、递减排序游标、argv |
| [AWFY/havlak](../bench/AWFY/an/havlak.an) | 否 | 无新增项 | 普通函数、u64 遍历和倒序边界、argv |
| [AWFY/json](../bench/AWFY/an/json.an) | 否 | 无新增项 | Parser 普通函数、DOC 字节、容器、argv |
| [AWFY/list](../bench/AWFY/an/list.an) | 是 | 无新增项 | Option 引用、递归和释放、argv |
| [AWFY/mand](../bench/AWFY/an/mand.an) | 是 | 无新增项 | 直接位移、u64 下标、argv |
| [AWFY/nbody](../bench/AWFY/an/nbody.an) | 是 | 无新增项 | 固定栈数组、浮点运算顺序、argv |
| [AWFY/permute](../bench/AWFY/an/permute.an) | 是 | 无新增项 | 入口堆计数器、重复调用复位、argv |
| [AWFY/queen](../bench/AWFY/an/queen.an) | 是 | 无新增项 | bool 标志切片、回溯索引/恢复、argv |
| [AWFY/richards](../bench/AWFY/an/richards.an) | 否 | 无新增项 | 普通函数、bool 状态、enum 分派、argv |
| [AWFY/sieve](../bench/AWFY/an/sieve.an) | 是 | 无新增项 | bool 标记、u64 下标、argv |
| [AWFY/storage](../bench/AWFY/an/storage.an) | 是 | 无新增项 | 入口堆计数器、栈 Random、引用递归、argv |
| [AWFY/towers](../bench/AWFY/an/towers.an) | 是 | 无新增项 | 引用链、递减退出、argv |
| [BG/binarytree](../bench/BG/an/binarytree.an) | 是 | 无新增项 | 结构体局部复制、res i32、深度/计数 u32、C 输出 |
| [BG/fann](../bench/BG/an/fann.an) | 是 | 无新增项 | for 语句范围、u64 下标、C 输出和退出 51 |
| [BG/fasta](../bench/BG/an/fasta.an) | 是 | 无新增项 | 堆 seed/checksum/概率表、const、C 输出 |
| [BG/revcomp](../bench/BG/an/revcomp.an) | 是 | 无新增项 | 同字节栈输入、400 字节表、左右边界、C 输出 |
| [BG/spectralnorm](../bench/BG/an/spectralnorm.an) | 是 | 无新增项 | 三个堆数组符合新规则、原 v 零初始化、C 输出 |
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
| [CVE-2022-3806](../cve/CVE-2022-3806/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-0341](../cve/CVE-2023-0341/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-1452](../cve/CVE-2023-1452/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-23086](../cve/CVE-2023-23086/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-26463](../cve/CVE-2023-26463/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-2905](../cve/CVE-2023-2905/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-2977](../cve/CVE-2023-2977/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-36273](../cve/CVE-2023-36273/README.md) | R05 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-39351](../cve/CVE-2023-39351/README.md) | R06 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-41361](../cve/CVE-2023-41361/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
| [CVE-2023-4264](../cve/CVE-2023-4264/README.md) | 无新增项 | 函数对应、对象表示与首次非法操作见案例 README |
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

本轮（v0.3 静态可变对象改写）：

- 低成本静态检查覆盖 60 对 / 147 个 .an 文件，873 处索引/范围表达式：解析、项目内函数/构造调用参数数目、
  custom impl 遗留及显式索引转换检查无错误。此项不是 147 文件的 native 类型检查。
- CD 和 fasta 精确 native --check 通过；分别覆盖标记引用在泛型树中的传递、堆概率表和标量状态类型。
- permute 当前源码用 O0 分别编译 raw/fat，并以 argv=2 运行：各执行两个工作单元，两个 8660 断言均通过、退出 0。
  同一 heap count 位于外层循环之前，benchmark 内只复位；没有执行默认大规模或采样测量。
- 精确选择 CVE-2023-4264 与 CVE-2023-49606，各只构建/执行 YIAN 侧，均 PASS、退出 1 / S002。
  分别保留服务内嵌 uri 首次越界写、删除 token 所属值后经旧 ptr 读取；C 侧本轮未重跑。
  证据取各自 build/cve/<ID>/yian-{build,run}.log，不将单案例 report.json 当作汇总执行报告。
- 其他已改写路径本轮仅作源码/静态检查，没有声称逐一运行通过。C 参考、编译器和标准库未修改。
- 父仓库与子模块 git diff --check 通过。

前轮局部证据仍按其当时源码与范围解读，不冒充本轮重跑：

- CVE-2021-32495 精确 --language both 运行通过：C ASan heap-use-after-free，READ of size 8，位置 free_object 的 data 字段；
  YIAN 退出 1，S003 dangling reference access。项目哈希（项目内全部文件、排除 build）：
  C `c8a2f280dcbd50aa75775e15ac06924618e3b114b47a057aa5ca11ab7f57d8cb`；
  YIAN `f86a90538bcc544d6f2816f2c4f8221caf5ad2f38fb4b41132200bb4d292ee54`。
- 前轮 queen raw/fat argv=1、bounce argv=1 与 representation 探针通过；JSON/Richards 等局部 --check 通过。
  这些源码本轮未变；RFX 的 S002 与 GIF 的 S006 单侧执行、若干 CVE build-only 也只是各自的局部证据。
- call_abi 的优化后 bitcode 证据见 R14；当前仍未解决。
- 未运行全部 38 个 CVE、全部 22 个基准、编译器整组测试或性能重测。
  旧 results/ 与论文样本保持为历史测量，不能作为当前改写源码的性能结果。

## 源码身份

本轮基线：子模块 292a8cf102d25984d8c229daf17e80657cb0cbee；改写后的源码身份见下列摘要。
源码集合 323 文件：38 个 C 项目的 .c/.h（排除 build）、38 个 YIAN src 的 .an，以及 22 对基准 .c/.an。
按相对路径排序，逐项拼接“路径 UTF-8 + NUL + 文件 SHA-256 的 32 字节摘要”，再求 SHA-256：

- 本轮改写前：37c9c5995ab5e2811c02ae1ebf3de6098a3a8f2f2aeeef89c878c28dde7a799a
- 当前：d775a34ebfe1f97a196e8b52e052e55caeb920c3f413871affd6616fcf44aad0

哈希用于确定被审查的源码，不证明语义等价。案例 README 与本报告记录应用和证据，规则仍只有 c-to-yian.md 一份。

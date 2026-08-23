# 机制索引：12 机制 → 规则号 → 负例形态 → CVE 归属

本文把胖指针安全机制的 12 个组成机制逐条索引：形式化出处（定义/规则号）、编译器内检查
落地点、负例的代码形态（在 CVE 用例中如何写出「触发该机制」的漏洞复刻）、以及已归属的
CVE（归属明细见文末「机制 ↔ CVE 归属表」与「完整归属明细」）。

出处约定：`F` = `docs/security.md`（形式化论证），`C` = `docs/security-code.md`（实现设计）。

## 机制清单

### 1. 5 字段表示（F §2.4 定义 11）

指针为五元组 `⟨data, lock_ptr, key, index, size⟩`，内联 40B 聚合（C §7.4 方案 A）。
一切检查的载体。

- **负例形态**：无独立触发形态——它是其余机制的前提；负例验证其副作用（如 `sizeof(T*)`
  为 40、指针数组布局）。
- **CVE 归属**：—（载体机制，无独立触发用例）。38 个 CVE 的全部运行期检查均以 5 字段
  表示为载体（间接依赖），但套件内无「以表示本身为触发点」的用例；直接布局验证见
  `tests/fat/positive/`。

### 2. Gen/键（F §2.3 定义 10）

键生成器：每次堆分配与栈帧进入调用；同类两次调用输出不同（单调计数器或 CSPRNG）。
键序列唯一性是 L-KEY（F §4.4）与作废永久性（L-NOKEY，F §4.5）的前提。

- **负例形态**：释放后块被复用、锁槽重激活写新键，旧指针键永不重新匹配——任何对旧指针
  的访问在 `live` 处 trap。形态与机制 5 相同，机制 2 是其「永不复活」的保证。
- **CVE 归属**：—（无直接 CVE：负例形态与机制 5 相同，套件内无独立堆风水/堆复用用例）。
  次级支持：`CVE-2025-47917`（UAF 读；其 MAPPING.md 注明 Gen/键提供作废永久性
  L-KEY/L-NOKEY，trap 点仍在 `live`）。直接触发形态见 `tests/fat/negative/`。

### 3. 锁槽（F §2.3 定义 7）

每个堆块/栈帧一个锁槽（块头/帧首一个机器字，锁头在负载之前），随对象携带；
释放/帧退出写 `SENTINEL` 后立即复用。时序检查载体。

- **负例形态**：无独立触发形态——承载机制 5/8 的检查点。
- **CVE 归属**：—（载体机制，无独立触发用例——承载机制 5/8 的检查点）。全部时序类
  CVE（机制 5/8 归属）间接依赖锁槽；直接形态验证见 `tests/fat/negative/`。

### 4. in_bounds（F §2.4 定义 12；规则 3.2.1-3.2.2/3.5.2）

全访问检查：`0 ≤ index ∧ index + n ≤ size`。读/写/字段取址的正常前提之一。

- **负例形态**：`p[i]` 越界读/写（`i ≥ size`）、one-past-end 访问（`index = size` 的指针
  不可读写）、多元素越界（结构体数组 `s[2].field` 类）→ `in_bounds` 失败 → trap。
- **CVE 归属**（25 个，全部期望 `Exit -4`；负例 `.an` = `tests/fat_cve/cve/<CVE-ID>/<CVE-ID>.an`）：
  - 写侧（规则 3.2.2 解引用写）：`CVE-2005-0102`（0 字节分配大拷贝）、`CVE-2016-5180`
    （转义尾点 off-by-one）、`CVE-2017-9995`（RLE 解码坐标未校验）、`CVE-2020-15999`
    （16 位截断分配）、`CVE-2021-3156`（unescape off-by-one）、`CVE-2021-32845`（负计数
    驱动 one-past-end）、`CVE-2021-37778`（strcpy 栈溢出）、`CVE-2023-0341`（glob 展开
    无检查写）、`CVE-2023-1452`（UTF-8 展开越界）、`CVE-2023-41361`（memcpy 无界拷贝）、
    `CVE-2023-4264`（strcpy 16B 缓冲）、`CVE-2023-49287`（strcpy 256B 缓冲）、
    `CVE-2024-47538`（max 非 min 循环上界）
  - 读侧（规则 3.2.1 解引用读）：`CVE-2022-31003`（one-past-end 读）、`CVE-2023-23086`
    （无闭引号扫描过界）、`CVE-2023-2905`（变长解码 4 字节上界）、`CVE-2023-2977`
    （ASN.1 tag 记账偏差）、`CVE-2023-36273`（CRC 长度越界）、`CVE-2023-39351`
    （零长度分配 `dyn[0]` 解引用）、`CVE-2024-37407`（`size_t` 下溢极大下标）、
    `CVE-2024-42478`（memcpy 超数据源）、`CVE-2024-45508`（whitespace-only 剥离读）、
    `CVE-2024-47777`（smpl chunk 偏移读）
  - 算术前提 CheckElementArith（定义 13，规则 3.3.1-3.3.2 指针算术良构，负下标经符号性
    扩宽成巨大偏移，先于写侧 in_bounds 拦截）：`CVE-2001-1009`（LIST 负消息号）、
    `CVE-2003-0721`（RFC2231 负续号）

### 5. live（F §2.3 定义 8；规则 3.2.1-3.2.2/3.6.2）

时序有效性：`μ⟨lock_ptr⟩ = key` 全字相等；null 指针短路为假。读/写/释放的正常前提。

- **负例形态**：`del p` 后访问 `*p`（UAF）；`del p` 两次（双释放，第二次在 `live` 处 trap）；
  释放后经另一派生指针访问（同对象派生指针共享锁槽与键，同步失效）。
- **CVE 归属**（10 个直接 + 2 个帧锁路径次级，全部期望 `Exit -4`）：
  - UAF 读（规则 3.2.1）：`CVE-2021-32495`（ref 槽清理释放共享对象）、`CVE-2021-39228`
    （inner_scope 释放登记缓冲）、`CVE-2022-0523`（错误分支释放后返回悬垂）、
    `CVE-2023-49606`（orderedmap_remove 释放后读 `*k`）、`CVE-2025-47917`（free 后经
    派生指针读）
  - UAF 字段访问/写（规则 3.2.2；`CVE-2022-3806` 另含 3.6.2）：`CVE-2022-3806`
    （refcount 字段访问，UAF 读先于第二次 del）、`CVE-2024-23310`（realloc 后旧指针写）
  - 双释放/无效释放（规则 3.6.2 delete 前提）：`CVE-2018-11410`（invalid free）、
    `CVE-2021-33304`（双释放兼 UAF，负例 trap 落点 3.2.1）、`CVE-2023-45664`
    （realloc(0) 后二次 free）
  - 帧锁路径次级（机制 8 用例的 `live` 检查点）：`CVE-2023-26463`、`CVE-2026-26399`

### 6. is_heap（F §2.3 定义 9；规则 3.6.2）

键最高位判定堆/栈（1 = 堆、0 = 栈）。释放前提之一：不得释放栈指针。

- **负例形态**：对栈局部取址指针 `del &x`（`is_heap` 失败）→ trap。
- **CVE 归属**：`CVE-2026-45959`（Linux crypto/ccp 的 `__cleanup(kfree)` 把栈变量地址
  传给释放函数；`del &x` 时键最高位 0 → 规则 3.6.2 释放前提第一项 `is_heap(p)` 不满足
  → trap，期望 `Exit -4`）。

### 7. is_raw（F §2.5；规则 3.6.2）

`data = lock_ptr + H ∧ index = 0` 纯字段检查：只允许释放「原始分配指针」。

- **负例形态**：`p + 1` 后 `del`（带偏移）；`&s.field` 后 `del`（子对象指针）→ trap。
- **CVE 归属**：—（套件内无偏移释放/子对象释放类 CVE；`CVE-2018-11410` 的 MAPPING.md
  注明其「带偏移指针形态」即属本机制，该批取 live 形态）。直接触发用例见
  `tests/fat/negative/`（`fat_del_offset_ptr` 等）。

### 8. 帧锁 re-key（F §2.6；规则 3.7.1-3.7.2）

每帧一个活动锁槽；帧进入 `Gen()` 生成新键、帧退出写 `SENTINEL`。同一帧取址共享
锁槽与键，帧退出后一并失效。

- **负例形态**：函数返回后访问其局部变量的取址指针（栈悬垂）→ 帧锁已写 SENTINEL、
  或复用帧获得新键 → `live` 失败 → trap。
- **CVE 归属**（2 个，规则 3.7.1-3.7.2，期望 `Exit -4`）：
  - `CVE-2023-26463`（CWE-562 悬垂返回指针：`tls_find_public_key` 返回 `&destroyed`
    栈局部，帧退出写 SENTINEL 后 main 解引用）
  - `CVE-2026-26399`（CWE-562：`pwm_start` 栈 handle 地址逃逸全局注册表，ISR 返回后
    解引用；帧锁失配 → trap）

### 9. 比较字段化（F §3.4；规则 3.4.1-3.4.2）

相等按 `(data, index)` 二元组（无前提，跨对象允许）；序比较前提 `data` 相等，
跨对象序比较 trap。CFG 层 `PtrCmp`/`CheckPtrCmp`（C §7.6 风险 3 实施状态）。

- **负例形态**：两个不同分配对象的指针做 `<`/`>`（跨对象序比较）→ trap。
- **CVE 归属**：—（套件内无依赖跨对象指针序比较的 CVE；直接触发用例见
  `tests/fat/negative/`）。

### 10. PtrDiff（F §3.3；规则 3.3.3）

指针差前提 `data` 相等（同对象）；异对象指针差 trap。CFG 层 `PtrDiff`。

- **负例形态**：两个不同分配对象的指针相减求长度 → trap（如 `ptr2 - ptr1` 当两者
  属不同对象）。
- **CVE 归属**：—（套件内无依赖异对象指针差的 CVE；直接触发用例见 `tests/fat/negative/`）。
  注：套件内合法同对象指针差（规则 3.3.3 前提满足）用例见 `CVE-2023-2977` 与
  `CVE-2024-37407` 的 MAPPING.md（`p - p0` / `r - name` 计算消耗字节）。

### 11. ZST（C §7.6 风险 2）

裁决：指针-to-ZST 保持 ZST 快路径（布局 `(0,1)`、malloc/load/store 短路），
不采用 40B 统一表示；`__is_fat_pointer` 以「PointerType 且 pointee 非 ZST」判定。

- **负例形态**：无检查点——指针-to-ZST 不构造 5 字段、不写锁槽。负例仅验证语义
  正确性（如 `dyn[0] T` 空数组分配、ZST 指针算术/比较不 trap）。
- **CVE 归属**：—（ZST 快路径无检查点，套件内无相关 CVE；语义正确性用例见
  `tests/fat/positive/`。注：`CVE-2023-39351` 的 `dyn[0] u8` 属普通 0 长度分配
  （非 ZST），仍走 5 字段路径、由机制 4 拦截）。

### 12. 受限操作（C §8.3）

`compiler/analysis/passes/restricted_ops.py`：`bitcast`、系统调用与运行时入口
（`sys_read`/`sys_write`/`open`/`close`/`__yian_*`）、构造器/搬移原语
（`from_raw_parts`/`assume_init`/`bitcopy`/`__memcpy`）仅限标准库；
标准库外出现即编译期 `AnalysisError`。`tests/std/` 豁免（功能测试），`tests/error/` 不豁免。

- **负例形态**：用户代码调用 `from_raw_parts(ptr, len)` 构造切片 / `bitcast` 重解释 /
  直接 `sys_write` → 编译期拒绝（`.err.an` 编译错误用例，非运行期 trap）。
- **CVE 归属**：—（编译期拒绝形态；38 个 CVE 复刻均为运行期 trap，不涉及）。该机制的
  `.err.an` 用例在 `tests/error/`，`tests/fat_cve/` 套件内无归属。

## 机制 ↔ CVE 归属表（38 CVE 全覆盖）

| 机制 | 已归属 CVE | 备注 |
|------|-----------|------|
| 1 5 字段表示 | — | 载体机制，无独立触发用例；全部 38 个 CVE 间接依赖 |
| 2 Gen/键 | — | 无直接 CVE；`CVE-2025-47917` 次级支持（作废永久性） |
| 3 锁槽 | — | 载体机制，承载机制 5/8 检查点 |
| 4 in_bounds | 25 个：CVE-2001-1009、CVE-2003-0721、CVE-2005-0102、CVE-2016-5180、CVE-2017-9995、CVE-2020-15999、CVE-2021-3156、CVE-2021-32845、CVE-2021-37778、CVE-2022-31003、CVE-2023-0341、CVE-2023-1452、CVE-2023-23086、CVE-2023-2905、CVE-2023-2977、CVE-2023-36273、CVE-2023-39351、CVE-2023-41361、CVE-2023-4264、CVE-2023-49287、CVE-2024-37407、CVE-2024-42478、CVE-2024-45508、CVE-2024-47538、CVE-2024-47777 | 含 2 个 CheckElementArith 算术前提（2001-1009/2003-0721，规则 3.3.1-3.3.2） |
| 5 live | 10 个：CVE-2018-11410、CVE-2021-32495、CVE-2021-33304、CVE-2021-39228、CVE-2022-0523、CVE-2022-3806、CVE-2023-45664、CVE-2023-49606、CVE-2024-23310、CVE-2025-47917 | 另 2 个帧锁路径次级：CVE-2023-26463、CVE-2026-26399 |
| 6 is_heap | 1 个：CVE-2026-45959 | 释放栈指针 |
| 7 is_raw | — | 套件内无偏移/子对象释放 CVE；触发形态见 tests/fat/negative/ |
| 8 帧锁 re-key | 2 个：CVE-2023-26463、CVE-2026-26399 | 栈悬垂/返回栈变量地址（CWE-562） |
| 9 比较字段化 | — | 套件内无跨对象序比较 CVE；见 tests/fat/negative/ |
| 10 PtrDiff | — | 套件内无跨对象指针差 CVE；见 tests/fat/negative/ |
| 11 ZST | — | ZST 快路径无检查点；语义用例见 tests/fat/positive/ |
| 12 受限操作 | — | 编译期拒绝形态；.err.an 用例在 tests/error/ |

合计：25（in_bounds）+ 10（live）+ 1（is_heap）+ 2（帧锁 re-key）= **38/38 CVE 全覆盖**；
每映射可追溯至负例 `.an`（`tests/fat_cve/cve/<CVE-ID>/<CVE-ID>.an`，期望 `Exit -4`）与
security.md 规则号，详见下表明细。

## 完整归属明细（38 CVE × 机制 × 规则号 × .an × trap）

| CVE-ID | 漏洞类型 | 机制 | 规则号 | 负例 `.an` | 期望 |
|--------|---------|------|--------|-----------|------|
| CVE-2001-1009 | LIST 负消息号负下标越界写（CWE-129） | 4 in_bounds（CheckElementArith） | 3.3.1-3.3.2 | `cve/CVE-2001-1009/CVE-2001-1009.an` | Exit -4 |
| CVE-2003-0721 | RFC2231 负续号负下标越界（CWE-129） | 4 in_bounds（CheckElementArith） | 3.3.1-3.3.2 | `cve/CVE-2003-0721/CVE-2003-0721.an` | Exit -4 |
| CVE-2005-0102 | 符号长度回绕 0 字节分配 → 拷贝越界写（CWE-190） | 4 in_bounds | 3.2.2 | `cve/CVE-2005-0102/CVE-2005-0102.an` | Exit -4 |
| CVE-2016-5180 | 转义尾点 off-by-one 堆越界写（CWE-787/193） | 4 in_bounds | 3.2.2 | `cve/CVE-2016-5180/CVE-2016-5180.an` | Exit -4 |
| CVE-2017-9995 | RLE 解码坐标未校验堆越界写（CWE-787） | 4 in_bounds | 3.2.2 | `cve/CVE-2017-9995/CVE-2017-9995.an` | Exit -4 |
| CVE-2018-11410 | invalid free（CWE-416） | 5 live | 3.6.2 | `cve/CVE-2018-11410/CVE-2018-11410.an` | Exit -4 |
| CVE-2020-15999 | 16 位截断分配堆越界写（CWE-787/190） | 4 in_bounds | 3.2.2 | `cve/CVE-2020-15999/CVE-2020-15999.an` | Exit -4 |
| CVE-2021-3156 | unescape off-by-one 堆越界写（CWE-193） | 4 in_bounds | 3.2.2 | `cve/CVE-2021-3156/CVE-2021-3156.an` | Exit -4 |
| CVE-2021-32495 | UAF 读（CWE-416） | 5 live | 3.2.1 | `cve/CVE-2021-32495/CVE-2021-32495.an` | Exit -4 |
| CVE-2021-32845 | 负计数驱动 one-past-end 越界写（CWE-787） | 4 in_bounds | 3.2.2 | `cve/CVE-2021-32845/CVE-2021-32845.an` | Exit -4 |
| CVE-2021-33304 | 双释放兼 UAF（CWE-415/416） | 5 live | 3.2.1/3.6.2 | `cve/CVE-2021-33304/CVE-2021-33304.an` | Exit -4 |
| CVE-2021-37778 | strcpy 栈缓冲越界写（CWE-121） | 4 in_bounds | 3.2.2 | `cve/CVE-2021-37778/CVE-2021-37778.an` | Exit -4 |
| CVE-2021-39228 | UAF 读（CWE-416） | 5 live | 3.2.1 | `cve/CVE-2021-39228/CVE-2021-39228.an` | Exit -4 |
| CVE-2022-0523 | 错误分支释放后返回悬垂 → UAF 读（CWE-416） | 5 live | 3.2.1/3.6.2 | `cve/CVE-2022-0523/CVE-2022-0523.an` | Exit -4 |
| CVE-2022-31003 | one-past-end 越界读（CWE-125） | 4 in_bounds | 3.2.1/3.3.1 | `cve/CVE-2022-31003/CVE-2022-31003.an` | Exit -4 |
| CVE-2022-3806 | UAF/双释放（CWE-416/415） | 5 live | 3.2.2/3.6.2 | `cve/CVE-2022-3806/CVE-2022-3806.an` | Exit -4 |
| CVE-2023-0341 | glob 展开栈越界写（CWE-121/787） | 4 in_bounds | 3.2.2/3.3.1 | `cve/CVE-2023-0341/CVE-2023-0341.an` | Exit -4 |
| CVE-2023-1452 | UTF-8 展开栈越界写（CWE-121/787） | 4 in_bounds | 3.2.2/3.3.1 | `cve/CVE-2023-1452/CVE-2023-1452.an` | Exit -4 |
| CVE-2023-23086 | 无闭引号扫描越界读（CWE-125） | 4 in_bounds | 3.2.1/3.3.1 | `cve/CVE-2023-23086/CVE-2023-23086.an` | Exit -4 |
| CVE-2023-26463 | 悬垂返回指针 + 空指针解引用（CWE-562/476） | 8 帧锁 re-key（+5 live） | 3.7.1-3.7.2 | `cve/CVE-2023-26463/CVE-2023-26463.an` | Exit -4 |
| CVE-2023-2905 | 变长解码越界读（CWE-125） | 4 in_bounds | 3.2.1 | `cve/CVE-2023-2905/CVE-2023-2905.an` | Exit -4 |
| CVE-2023-2977 | ASN.1 tag 记账偏差越界读（CWE-125） | 4 in_bounds | 3.2.1 | `cve/CVE-2023-2977/CVE-2023-2977.an` | Exit -4 |
| CVE-2023-36273 | CRC 长度越界读（CWE-125） | 4 in_bounds | 3.2.1 | `cve/CVE-2023-36273/CVE-2023-36273.an` | Exit -4 |
| CVE-2023-39351 | 零长度分配解引用（CWE-476） | 4 in_bounds | 3.2.1 | `cve/CVE-2023-39351/CVE-2023-39351.an` | Exit -4 |
| CVE-2023-41361 | memcpy 无界拷贝越界写（CWE-787） | 4 in_bounds | 3.2.2 | `cve/CVE-2023-41361/CVE-2023-41361.an` | Exit -4 |
| CVE-2023-4264 | strcpy 16B 缓冲栈/全局越界写（CWE-121） | 4 in_bounds | 3.2.2/3.3.1 | `cve/CVE-2023-4264/CVE-2023-4264.an` | Exit -4 |
| CVE-2023-45664 | realloc(0) 双重释放（CWE-415） | 5 live | 3.6.2 | `cve/CVE-2023-45664/CVE-2023-45664.an` | Exit -4 |
| CVE-2023-49287 | strcpy 256B 缓冲栈越界写（CWE-121） | 4 in_bounds | 3.2.2 | `cve/CVE-2023-49287/CVE-2023-49287.an` | Exit -4 |
| CVE-2023-49606 | UAF 读（CWE-416） | 5 live | 3.2.1 | `cve/CVE-2023-49606/CVE-2023-49606.an` | Exit -4 |
| CVE-2024-23310 | realloc 旧指针 UAF 写（CWE-416） | 5 live | 3.2.2 | `cve/CVE-2024-23310/CVE-2024-23310.an` | Exit -4 |
| CVE-2024-37407 | size_t 下溢极大下标越界读（CWE-125） | 4 in_bounds | 3.2.1 | `cve/CVE-2024-37407/CVE-2024-37407.an` | Exit -4 |
| CVE-2024-42478 | memcpy 超数据源越界读（CWE-125） | 4 in_bounds | 3.2.1 | `cve/CVE-2024-42478/CVE-2024-42478.an` | Exit -4 |
| CVE-2024-45508 | whitespace-only 剥离越界读（CWE-125/787） | 4 in_bounds | 3.2.1 | `cve/CVE-2024-45508/CVE-2024-45508.an` | Exit -4 |
| CVE-2024-47538 | max 循环上界栈越界写（CWE-121/787） | 4 in_bounds | 3.2.2 | `cve/CVE-2024-47538/CVE-2024-47538.an` | Exit -4 |
| CVE-2024-47777 | smpl chunk 偏移越界读（CWE-125） | 4 in_bounds | 3.2.1 | `cve/CVE-2024-47777/CVE-2024-47777.an` | Exit -4 |
| CVE-2025-47917 | UAF 读（CWE-416；Gen/键 次级支持） | 5 live（+2 Gen/键） | 3.2.1/3.6.2 | `cve/CVE-2025-47917/CVE-2025-47917.an` | Exit -4 |
| CVE-2026-26399 | 返回栈变量地址（CWE-562） | 8 帧锁 re-key（+5 live） | 3.7.1-3.7.2/3.2.1 | `cve/CVE-2026-26399/CVE-2026-26399.an` | Exit -4 |
| CVE-2026-45959 | 释放栈指针（CWE-590/761） | 6 is_heap | 3.6.2 | `cve/CVE-2026-45959/CVE-2026-45959.an` | Exit -4 |

## 与既有负例的关系

`tests/fat/negative/` 已覆盖各机制的直接触发形态（越界、UAF、双释放、栈悬垂、
带偏移释放、释放栈指针、跨对象 ptrdiff/cmp 等），CVE 用例是在其基础上的**真实漏洞
复刻**：把「抽象负例形态」包装成真实 CVE 的代码结构（真实调用序列、真实对象布局、
真实利用链的触发路径）。迁移时按 `docs/SYNTAX.md` 把 C 风格源码改写为胖指针语法，
漏洞触发点必须落在上表对应机制的检查点。

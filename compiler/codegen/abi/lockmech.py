"""胖指针时序机制层——锁表 / 键 / 帧锁（机制定义；LLVM 值层下降由 LLVM 层完成）。

本模块承载胖指针内存安全机制的运行时概念，对应理论篇 docs/security.md 与本模块的
CFG/LLVM 实现：

- **指针的第二个字** `word = ⟨key:32 | lock:32⟩`（lock 在低位，见 `WORD_KEY_SHIFT`）：
  `key` 是这一轮生命周期的身份，`lock` 是全局锁表的下标。
- **锁表** `__secl_lock_table`：表项 8 B = `{key:u32 @0, anchor_lo32:u32 @4}`
  （见 `LockEntry`）。下标区间自带 kind：`[0, FRAME_LOCK_SLOTS)` 是帧影子栈槽
  （下标 = 深度），接着两个字面量/环境常量槽，其余由分配器发放给堆对象。
  热路径没有 kind 分派，锁槽地址只由 `word` 的 lock 字段给出。
- **谓词**（LLVM 层内联发射，本模块给出同一语义）：
  `live` = `word ≠ 0 ∧ load32(锁表[lock].key) == key`；
  `is_heap` = lock 落在堆区间（纯下标判定，不读锁表）；
  `is_raw` = 负载锚与 index 的纯字段检查。
- **块头** `BlockHeader`：8 B = `{extent:u32 @0, pad:u32 @4}`，负载 = 块首 + 8。
  身份（key）在锁表项里，指针携带同一个 key。
- **帧锁**：每帧一个活动槽，帧进入 re-key（`k_f ← Gen()`，32 位键体）、帧退出把槽写成
  `SENTINEL`（全部返回路径），槽位来自固定地址的独立影子栈 `FrameLockArena`。
- **堆分配生命周期**：分配时由 LLVM 层写表项 `key`/`anchor_lo32`，`del` 把表项 key +1
  换代（悬垂指针立刻失配）并把下标压回自由链。

本模块定义常量、布局、协议与谓词语义；CFG 层负责插入检查，LLVM 层负责值下降与运行期
失败协议。`runtime/include/yian_rt.h` 是 ABI 常量的 C 侧真值，`runtime/build.py --check`
断言两侧一致（帧槽数/槽宽/块头字节数/尺寸类表/失败消息）。
"""

from __future__ import annotations

from typing import ClassVar

# ---------------------------------------------------------------------------
# word 编码与键 / 哨兵
# ---------------------------------------------------------------------------

WORD_LOCK_SHIFT = 0
"""lock 字段在 word 里的移位（低位）。"""

LOCK_BITS = 32
"""lock 字段位宽（表下标；表实际只有 2^26 项，高位留白）。"""

LOCK_MASK = (1 << LOCK_BITS) - 1
"""lock 字段掩码（word 低 32 位）。"""

WORD_KEY_SHIFT = 32
"""key 字段在 word 里的移位（高位）。"""

KEY_MASK = 0xFFFF_FFFF
"""key 字段掩码：32 位键，与锁表项里的 u32 同宽。"""

FRAME_KEY_LIMIT = KEY_MASK - 1
"""帧键计数上限；0xFFFFFFFF 留给帧退出的 SENTINEL，绝不回绕（回绕会让旧指针重新匹配）。"""

SENTINEL = 0xFFFF_FFFF_FFFF_FFFF
"""哨兵值 = 全 1 字（编码约定）。

帧退出把**帧锁槽**写成它（LLVM 层发射 volatile 写）；32 位键比较取到的低 32 位是
0xFFFFFFFF，而活动键最大只到 `FRAME_KEY_LIMIT`，因此任何活指针都与哨兵失配。堆路径
不写哨兵：`del` 把锁表项的 key +1 换代（见 `LockEntry`），同样让悬垂指针失配。
"""

# 视图长度字段位宽与上限：PointerType 的 index/size 压到 32 位（元素数）。
# 分配元素数在 Malloc 处校验 ≤ MAX_VIEW_COUNT（超限报 R001）；视图长度都来自某个
# 分配的容量，因此指针携带的长度分量必然可表示。
VIEW_COUNT_BITS = 32
MAX_VIEW_COUNT = (1 << VIEW_COUNT_BITS) - 1

# 4 GiB 窗口：用"数据地址高 32 位 + 低 32 位字段"还原负载锚，因此一个块的
# [块首, 块首 + 块头 + 负载) 必须整体落在同一窗口内（分配器保证）。
WINDOW_MASK = ((1 << 64) - 1) ^ LOCK_MASK

# 锁表下标区间（顺序即 kind）。
FRAME_LOCK_SLOTS = 1 << 20
LITERAL_LOCK_INDEX = FRAME_LOCK_SLOTS
ENV_LOCK_INDEX = FRAME_LOCK_SLOTS + 1
HEAP_LOCK_BASE = FRAME_LOCK_SLOTS + 2
LOCK_ENTRY_BYTES = 8
LOCK_TABLE_SLOTS = 1 << 26

# 字面量/环境常量槽：key 恒 0、永不变更，因此这些指针恒 live。word 的 lock 字段
# 就是上表的常量下标。
LITERAL_KEY = 0
ENV_KEY = 0
LITERAL_WORD = (LITERAL_KEY << WORD_KEY_SHIFT) | LITERAL_LOCK_INDEX
ENV_WORD = (ENV_KEY << WORD_KEY_SHIFT) | ENV_LOCK_INDEX

# 5 字段胖指针字段下标（PointerType 映射为
# {data: ptr, word: u64, index: u32, size: u32} 24B 聚合）。
FAT_DATA = 0
FAT_WORD = 1
FAT_INDEX = 2
FAT_SIZE = 3

# 4 字段 slice 字段下标：{data: ptr, word: u64, size: u64} 24B。
SLICE_DATA = 0
SLICE_WORD = 1
SLICE_SIZE = 2

# 3 字段引用字段下标：{data: ptr, word: u64} 16B。
REF_DATA = 0
REF_WORD = 1


def is_heap(lock: int) -> bool:
    """`is_heap(p)` 纯下标判定——锁表下标落在堆区间（≥ HEAP_LOCK_BASE）。"""
    return lock >= HEAP_LOCK_BASE


def live(word: int, slot_key: int) -> bool:
    """`live(p)` = word ≠ 0 ∧ 锁表项 key == 指针携带的 key。

    LLVM 层发射的形态是"先按 word 的低 32 位取表项、读 32 位 key 比较，再与
    word ≠ 0 相与"（`word = 0` 时取到的是表项 0，比较结果不参与判定）。
    """
    if word == 0:
        return False
    return slot_key == ((word >> WORD_KEY_SHIFT) & KEY_MASK)


def is_raw(data: int, anchor: int, index: int) -> bool:
    """`is_raw`：纯字段检查——`(data & LOCK_MASK) == anchor ∧ index == 0`。

    `anchor` 是锁表项里的 `anchor_lo32`（负载锚地址的低 32 位）。比较只看低 32 位，
    依赖"块整体落在同一 4 GiB 窗口"这一分配器保证（见 `WINDOW_MASK`）；不读锁槽内容。
    重锚定子对象指针（`&s.field`）与偏移指针（`p ± n`、`&arr[i≠0]`）由此偏离原始锚点、
    被 `del` 拒绝。
    """
    return (data & LOCK_MASK) == anchor and index == 0


class BlockHeader:
    """单线程堆池的块头布局。

    ``{extent:u32 @0, pad:u32 @4}`` = 8 B；负载 = 块首 + 8。`extent` 是这次分配的
    **元素数**（u32；删除/视图检查按指针元素大小折算字节）；身份（key）在锁表项里，
    指针携带同一个 key，`pad` 留白以保持负载锚 8 字节对齐（语言最大对齐 = 8）。
    """

    BYTES: ClassVar[int] = 8
    EXTENT_OFFSET: ClassVar[int] = 0  # 当前负载元素数(u32)

    @staticmethod
    def data_addr(block_base: int) -> int:
        """负载锚地址：`data = b + H`（分配锚定）。"""
        return block_base + BlockHeader.BYTES


class LockEntry:
    """锁表项布局：``{key:u32 @0, anchor_lo32:u32 @4}`` = 8 B。

    key 是这一轮生命周期的身份（发放时 +1；释放时 +1，悬垂指针立刻失配）；
    anchor_lo32 是负载锚地址的低 32 位，删除/视图用它 + data 高 32 位还原块首。
    空闲表项把"下一空闲下标 + 1"写在自己的 anchor 字段里，链头在运行时的
    ``__secl_lock_free_head``。帧槽与字面量/环境槽只用 key 字段。
    """

    BYTES: ClassVar[int] = LOCK_ENTRY_BYTES
    KEY_OFFSET: ClassVar[int] = 0
    ANCHOR_OFFSET: ClassVar[int] = 4


class FrameLockArena:
    """单线程稳定帧锁影子栈布局。

    槽位是进程生命期内固定地址的 ``u64`` 数组元素，仅由受信任的帧进入/退出协议读写。
    活动帧按 LIFO 次序占用槽位；退出写 ``SENTINEL`` 后弹出，后续复用在任何用户步之前
    写入新键。容量耗尽与键耗尽一样确定性失败，不回绕或退化到普通栈存储。

    2^20 个槽位保留 8 MiB 虚拟地址空间；页在访问时按需常驻，因此物理开销与峰值同时
    活动的取址帧数成正比。
    """

    SLOTS: ClassVar[int] = 1 << 20
    SLOT_BYTES: ClassVar[int] = 8


# ---------------------------------------------------------------------------
# stdlib 边界 fat 合成（机制定义，执行依赖 CFG 层/LLVM 层）
#
# lib/core/slice.an 的 SliceStruct<T> 是源代码层包装结构：默认 fat 模式下
# T* 字段为 24B，加 len 字段后更大；raw 模式下为 8B + 8B。
# SliceType/StrType 是独立的内置视图类型：fat 映射为 {data, word, size} 24B，
# raw 映射为 {T*, u64} 16B。构造点的字段合成方式为 ⟨data, word, size⟩，
# 其中 data = 被退化/取址的负载地址，word 从源胖指针继承（取址点的帧锁或堆块锁），
# size = 切片或字符串长度。字段下标见 SLICE_* 常量。
#
# 元数据在创建点注入（VarPtr / Malloc 的 CFG/LLVM 下降）随数据流自然流入，
# 本模块不提供独立 fat 构造原语（YIAN 代码不能调用编译器内部合成）。
# ---------------------------------------------------------------------------

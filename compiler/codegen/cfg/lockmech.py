"""胖指针时序机制层——块头锁槽 / 键 / 帧锁(机制定义;LLVM 值层下降由 LLVM 层完成)。

本模块承载胖指针内存安全机制的运行时概念,对应理论篇 docs/security.md 与
本模块的 CFG/LLVM 实现:

- 编码约定:`LockEntry` 编码为单个机器字;`SENTINEL` = 全 1 字(~0)。
- 块布局与锁槽 `LockSlot`:堆块使用固定 16B 元数据头(锁槽 + 当前负载长度),
  块头区 [b, b+H) 在负载之前;分配锚定 data = b + H。帧锁位于
  固定地址的独立影子栈,不与普通栈帧共用存储。
- `live`:锁槽键比较(全字相等);p.lock_ptr = 0(null 编码)短路为假。
- `is_heap`:键最高位纯位判定(0 = 栈、1 = 堆),不读锁槽。
- `Gen`:单调计数器(预决;CSPRNG 不做),堆/栈各维护独立 63 位计数,
  键 = 标志位(最高位)拼接计数。
- 堆分配生命周期协议(分配写键 / 释放写 SENTINEL / `is_raw` 纯字段检查)。
- 栈帧进入/退出协议(帧锁 re-key / 帧退出写 SENTINEL——全部返回路径)。
- 块头锁槽与帧锁槽的写点:Malloc 写键、Delete 写 SENTINEL、
  帧进入 re-key、帧退出写 SENTINEL。

本模块定义常量、布局、协议与谓词语义；CFG 层负责插入检查，LLVM 层负责值下降
与运行期失败协议。不提供独立 fat 构造原语
(stdlib 边界 fat 合成为注释定义,见文末)。
"""

from __future__ import annotations

from typing import ClassVar

# ---------------------------------------------------------------------------
# 键 / 哨兵 / 锁槽值编码(编码约定)
# ---------------------------------------------------------------------------

KEY_BITS = 64
"""键与锁槽值宽度(位)。"""

FLAG_BIT = KEY_BITS - 1
"""最高位下标(63),承载堆/栈标志。"""

FLAG_MASK = 1 << FLAG_BIT
"""最高位掩码(0x8000_0000_0000_0000):`is_heap` 纯位判定用。"""

BODY_MASK = FLAG_MASK - 1
"""键体掩码(低 63 位),单调计数体。"""

MAX_HEAP_BODY = BODY_MASK - 1
"""最大堆键体；全 1 键体保留给 ``SENTINEL``。"""

MAX_STACK_BODY = BODY_MASK
"""最大栈键体；最高位为 0，故不会与 ``SENTINEL`` 重合。"""

STACK_FLAG = 0
"""栈键最高位 0。"""

HEAP_FLAG = 1
"""堆键最高位 1。"""

SENTINEL = (1 << KEY_BITS) - 1
"""哨兵值 = 全 1 字(~0, 编码约定)。

释放 `delete p` 把块头锁槽写成 SENTINEL;帧退出把帧锁槽
写成 SENTINEL。哨兵与普通数据一样由统一值失配(v != k)
闭合时序检查。
"""

# 视图长度字段位宽与上限:PointerType 的 index/size 压到 32 位(元素数)。
# 分配元素数在 Malloc 处校验 ≤ MAX_VIEW_COUNT(超限报 R001); 视图长度都来自某个
# 分配的容量, 因此指针携带的长度分量必然可表示 —— 只有"长度来自一个不存在的分配"
# 的原始构造才会被静默截断(见 docs/grammar/16.runtime_errors.md)。
VIEW_COUNT_BITS = 32
MAX_VIEW_COUNT = (1 << VIEW_COUNT_BITS) - 1

# word 编码(变体 B): 指针的第二个字 = ⟨key:32 | lock:32⟩(lock 在低位)。
#
#   live(p) = word != 0 ∧ load32(锁表[lock].key) == key
#
# 锁槽全部落在一张全局表里: lock 就是表下标, 地址 = 表基址 + lock*LOCK_ENTRY_BYTES,
# 与 data / kind 都无关 —— 热路径没有 kind 分派、没有窗口公式。
# 下标区间自带 kind: [0, FRAME_LOCK_SLOTS) 帧槽(下标 = 深度), 接着两个字面量/环境常量槽,
# 其余由分配器发放给堆对象。
WORD_LOCK_SHIFT = 0
LOCK_BITS = 32
LOCK_MASK = (1 << LOCK_BITS) - 1
WORD_KEY_SHIFT = 32
KEY_BITS = 32
KEY_MASK = (1 << KEY_BITS) - 1
FRAME_KEY_LIMIT = KEY_MASK - 1     # 0xFFFFFFFF 留给帧退出的 SENTINEL

WINDOW_MASK = ((1 << 64) - 1) ^ LOCK_MASK   # 冷路径还原块首用的 4 GiB 窗口掩码
FRAME_LOCK_SLOTS = 1 << 20
LITERAL_LOCK_INDEX = FRAME_LOCK_SLOTS
ENV_LOCK_INDEX = FRAME_LOCK_SLOTS + 1
HEAP_LOCK_BASE = FRAME_LOCK_SLOTS + 2
LOCK_ENTRY_BYTES = 8
LOCK_TABLE_SLOTS = 1 << 26

LITERAL_KEY = 0
ENV_KEY = 0
LITERAL_WORD = (LITERAL_KEY << WORD_KEY_SHIFT) | LITERAL_LOCK_INDEX
ENV_WORD = (ENV_KEY << WORD_KEY_SHIFT) | ENV_LOCK_INDEX

# 5 字段胖指针字段下标(PointerType 映射为
# {data: ptr, word: u64, index: u32, size: u32} 24B 聚合)。
FAT_DATA = 0
FAT_WORD = 1
FAT_INDEX = 2
FAT_SIZE = 3

# 4 字段 slice 字段下标: {data: ptr, word: u64, size: u64} 24B。
SLICE_DATA = 0
SLICE_WORD = 1
SLICE_SIZE = 2

# 3 字段引用字段下标: {data: ptr, word: u64} 16B。
REF_DATA = 0
REF_WORD = 1


class KeyGen:
    """Gen 单调计数器(预决:单调计数器;CSPRNG 不做)。

    堆分配与栈帧进入各维护独立的 63 位计数,输出键 = 标志位(堆 1 / 栈 0,
    最高位)拼接计数。同类内任意两次调用输出不同且严格更大;跨类因最高位
    不同恒不相等——L-KEY(i) 序列唯一性由此成立(security.md §4.4)。

    LLVM 层发射(每次分配 / 帧进入的 `k ← Gen()`)由 LLVM 层完成;
    本类承载机制语义。
    """

    def __init__(self) -> None:
        self.__heap_counter = 0
        self.__stack_counter = 0

    def heap_key(self) -> int:
        """`k ← Gen()`,堆键最高位 1(块头锁槽写键)。"""
        if self.__heap_counter >= MAX_HEAP_BODY:
            raise OverflowError("Gen heap counter exhausted (SENTINEL reserved)")
        self.__heap_counter += 1
        return FLAG_MASK | self.__heap_counter

    def stack_key(self) -> int:
        """`k_f ← Gen()`,栈键最高位 0(帧进入 re-key)。"""
        if self.__stack_counter >= MAX_STACK_BODY:
            raise OverflowError("Gen stack counter exhausted (63-bit space)")
        self.__stack_counter += 1
        return self.__stack_counter


def is_heap(lock: int) -> bool:
    """`is_heap(p)` 纯下标判定——锁表下标落在堆区间(≥ HEAP_LOCK_BASE)。"""
    return lock >= HEAP_LOCK_BASE


def live(word: int, slot_key: int) -> bool:
    """`live(p)` = word ≠ 0 ∧ 锁表项 key == 指针携带的 key。

    锁表地址只由 word 的 lock 下标给出; null(word = 0)短路为假——不读表项 0
    (否则会把尚未发放的帧槽误判成活)。
    """
    if word == 0:
        return False
    return slot_key == (word >> WORD_KEY_SHIFT)


def is_raw(data: int, block: int, index: int) -> bool:
    """`is_raw`:纯字段检查——`data == block + H` 且 `index == 0`。

    block 是重建出的块首(锁槽地址);不读锁槽。重锚定子对象指针(&s.field)与
    偏移指针(p ± n、&arr[i≠0])由此偏离原始锚点、被 `delete` 拒绝(前提)。
    """
    return data == block + BlockHeader.BYTES and index == 0


class BlockHeader:
    """SecL 单线程堆池的块头布局(变体 B)。

    ``{extent:u32 @0, pad:u32 @4}`` = 8 B; 负载 = block + 8。extent 是这次分配的
    **元素数**(u32; 删除/视图检查按指针元素大小折算字节); 身份(key)在锁表项里,
    指针携带同一个 key。块头留 8 B 是为了负载锚保持 8 字节对齐(语言最大对齐 = 8)。
    """

    BYTES: ClassVar[int] = 8
    EXTENT_OFFSET: ClassVar[int] = 0  # 当前负载元素数(u32)

    @staticmethod
    def data_addr(block_base: int) -> int:
        """负载锚地址:`data = b + H`(分配锚定)。"""
        return block_base + BlockHeader.BYTES


class LockEntry:
    """锁表项布局: ``{key:u32 @0, anchor_lo32:u32 @4}`` = 8 B。

    key 是这一轮生命周期的身份(发放时 +1; 释放时 +1, 悬垂指针立刻失配);
    anchor_lo32 是负载锚地址的低 32 位, 冷路径(删除/视图)用它 + data 高位还原块首。
    空闲表项把"下一空闲下标 + 1"写在自己的 anchor 字段里, 链头在运行时的
    ``__secl_lock_free_head``。帧槽与字面量/环境槽只用 key 字段。
    """

    BYTES: ClassVar[int] = LOCK_ENTRY_BYTES
    KEY_OFFSET: ClassVar[int] = 0
    ANCHOR_OFFSET: ClassVar[int] = 4


class FrameLockArena:
    """单线程稳定帧锁影子栈布局。

    槽位是进程生命期内固定地址的 ``u64`` 数组元素,仅由受信任的
    帧进入/退出协议读写。活动帧按 LIFO 次序占用槽位;退出写
    ``SENTINEL`` 后弹出,后续复用在任何用户步之前写入新键。容量耗尽
    与键耗尽一样确定性失败,不回绕或退化到普通栈存储。

    2^20 个槽位保留 8 MiB 虚拟地址空间;页在访问时按需常驻,
    因此物理开销与峰值同时活动的取址帧数成正比。
    """

    SLOTS: ClassVar[int] = 1 << 20
    SLOT_BYTES: ClassVar[int] = 8


class FrameLock:
    """帧锁:每帧一个活动锁槽。

    - 帧进入 re-key:`k_f ← Gen()`(栈键最高位 0),帧锁槽写键 μ⟨e_f⟩ := k_f;
      即使帧被递归 / 循环复用于同一锁槽,新键与上次调用生成的不同。
    - 帧退出写 SENTINEL:μ⟨e_f⟩ := SENTINEL —— **全部返回路径**(多返回函数
      每条 return 前插 SENTINEL 写)。
    - 帧锁槽位置(预决)= 独立稳定影子栈槽位;槽位不属于普通函数栈帧,
      不会被用户局部变量复用。函数入口协议维护 ⟨e_f, k_f⟩ 并传给
      每个取址点(VarPtr 合成 ⟨a_x, e_f, k_f, 0, 1⟩,CFG 层)。

    LLVM 层发射(影子栈 push + re-key、每条 return 前写 SENTINEL 并 pop)由 LLVM 层完成;
    本类承载帧锁机制语义与值。
    """

    def __init__(self, gen: KeyGen, slot_address: int) -> None:
        self.__gen = gen
        self.__slot_address = slot_address
        self.__key: int = 0

    def enter(self) -> int:
        """帧进入 re-key:`k_f ← Gen()`,返回 k_f。"""
        self.__key = self.__gen.stack_key()
        return self.__key

    def exit(self) -> int:
        """帧退出:返回 SENTINEL,由调用方写入帧锁槽。"""
        return SENTINEL

    def current(self) -> tuple[int, int]:
        """当前帧锁 ⟨e_f, k_f⟩(帧进入后);取址点 / stdlib 边界 fat 合成读取。"""
        return (self.__slot_address, self.__key)


# ---------------------------------------------------------------------------
# stdlib 边界 fat 合成(机制定义,执行依赖 CFG 层/LLVM 层)
#
# lib/core/slice.an 的 SliceStruct<T> 是源代码层包装结构:默认 fat 模式下
# T* 字段为 40B,加 len 字段后为 48B;raw 模式下为 8B + 8B = 16B。
# SliceType/StrType 是独立的内置视图类型:fat 映射为
# {data, lock_ptr, key, size} 32B,raw 映射为 {T*, u64} 16B。
# fat 切片/字符串构造创建点的 4 字段合成方式为:
#
#     ⟨data, lock_ptr, key, len⟩
#
# 其中 data = 被退化/取址的负载地址,lock_ptr/key 从源胖指针继承
# (取址点的帧锁或堆块锁);size = len(切片或字符串长度)。字段下标见
# SLICE_* 常量。仅在无法继承元数据的低层回退路径才使用专用合成逻辑。
#
# 元数据在创建点注入(VarPtr / Malloc 的 CFG/LLVM 下降)随数据流自然流入,
# 本模块不提供独立 fat 构造原语(stdlib YIAN 代码不能调用编译器内部合成)。
# ---------------------------------------------------------------------------

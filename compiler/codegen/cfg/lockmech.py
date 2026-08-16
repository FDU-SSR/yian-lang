"""胖指针时序机制层——块头锁槽 / 键 / 帧锁(机制定义;LLVM 值层下降属 t8)。

本模块承载胖指针内存安全机制的运行时概念,对应理论篇 docs/security.md 与
实现篇 docs/security-code.md §7.1:

- 定义 6(编码约定):`LockEntry` 编码为单个机器字;`SENTINEL` = 全 1 字(~0)。
- 定义 7(块布局与锁槽 `LockSlot`):块首 H 字节锁槽(锁头仅锁槽,H = w = 8B),
  锁头区 [b, b+H) 在负载之前;分配锚定 data = b + H。栈帧锁头仅含锁槽。
- 定义 8(`live`):锁槽键比较(全字相等);p.lock_ptr = 0(null 编码)短路为假。
- 定义 9(`is_heap`):键最高位纯位判定(0 = 栈、1 = 堆),不读锁槽。
- 定义 10(`Gen`):单调计数器(预决;CSPRNG 不做),堆/栈各维护独立 63 位计数,
  键 = 标志位(最高位)拼接计数。
- §2.5 堆分配生命周期协议(分配写键 / 释放写 SENTINEL / `is_raw` 纯字段检查)。
- §2.6 栈帧进入/退出协议(帧锁 re-key / 帧退出写 SENTINEL——全部返回路径)。
- 规则 3.6.1(Malloc 块头写键)、3.6.2(Delete 写 SENTINEL)、
  3.7.1(帧进入 re-key)、3.7.2(帧退出写 SENTINEL)。

本 todo(t9)只交付机制代码存在性:常量、布局、协议与谓词语义;CFG 检查插入
属 t7,LLVM 值层下降与运行期 trap 属 t8。不提供独立 fat 构造原语
(stdlib 边界 fat 合成为注释定义,见文末)。
"""

from __future__ import annotations

from typing import ClassVar

# ---------------------------------------------------------------------------
# 键 / 哨兵 / 锁槽值编码(定义 5-6,编码约定)
# ---------------------------------------------------------------------------

KEY_BITS = 64
"""键与锁槽值宽度(位)。"""

FLAG_BIT = KEY_BITS - 1
"""最高位下标(63),承载堆/栈标志(定义 9)。"""

FLAG_MASK = 1 << FLAG_BIT
"""最高位掩码(0x8000_0000_0000_0000):`is_heap` 纯位判定用。"""

BODY_MASK = FLAG_MASK - 1
"""键体掩码(低 63 位),单调计数体。"""

STACK_FLAG = 0
"""栈键最高位 0(定义 9)。"""

HEAP_FLAG = 1
"""堆键最高位 1(定义 9)。"""

SENTINEL = (1 << KEY_BITS) - 1
"""哨兵值 = 全 1 字(~0,定义 6 编码约定)。

释放 `delete p` 把块头锁槽写成 SENTINEL(规则 3.6.2 动作①);帧退出把帧锁槽
写成 SENTINEL(规则 3.7.2 动作①)。哨兵与普通数据一样由统一值失配(v != k)
闭合时序检查(定义 8)。
"""

# 5 字段胖指针字段下标(§7.4 方案 A;t6 已把 PointerType 映射为
# {data: ptr, lock_ptr: ptr, key: u64, index: u64, size: u64} 40B 聚合)。
FAT_DATA = 0
FAT_LOCK_PTR = 1
FAT_KEY = 2
FAT_INDEX = 3
FAT_SIZE = 4

# 4 字段 slice/str 字段下标(t2 tiered-pointers 表示层,删 index):
# {data: ptr, lock_ptr: ptr, key: u64, size: u64} 32B。data/lock_ptr/key 与
# 5 字段指针同下标(FAT_DATA/FAT_LOCK_PTR/FAT_KEY 通用),仅 size 为下标 3。
SLICE_DATA = 0
SLICE_LOCK_PTR = 1
SLICE_KEY = 2
SLICE_SIZE = 3

# 3 字段引用字段下标(t2,t1 临时同 5 字段布局已替换):{data, lock_ptr, key} 24B。
# 引用不携带 index/size——引用恒指向单个元素。
REF_DATA = 0
REF_LOCK_PTR = 1
REF_KEY = 2


class KeyGen:
    """Gen 单调计数器(定义 10,预决:单调计数器;CSPRNG 不做)。

    堆分配与栈帧进入各维护独立的 63 位计数,输出键 = 标志位(堆 1 / 栈 0,
    最高位)拼接计数。同类内任意两次调用输出不同且严格更大;跨类因最高位
    不同恒不相等——L-KEY(i) 序列唯一性由此成立(security.md §4.4)。

    LLVM 层发射(每次分配 / 帧进入的 `k ← Gen()`,规则 3.6.1 / 3.7.1)属 t8;
    本类承载机制语义。
    """

    def __init__(self) -> None:
        self.__heap_counter = 0
        self.__stack_counter = 0

    def heap_key(self) -> int:
        """`k ← Gen()`,堆键最高位 1(规则 3.6.1 块头锁槽写键)。"""
        self.__heap_counter += 1
        if self.__heap_counter > BODY_MASK:
            raise OverflowError("Gen heap counter exhausted (63-bit space)")
        return FLAG_MASK | self.__heap_counter

    def stack_key(self) -> int:
        """`k_f ← Gen()`,栈键最高位 0(规则 3.7.1 帧进入 re-key)。"""
        self.__stack_counter += 1
        if self.__stack_counter > BODY_MASK:
            raise OverflowError("Gen stack counter exhausted (63-bit space)")
        return self.__stack_counter


def is_heap(key: int) -> bool:
    """定义 9:`is_heap(p)` 纯位判定——`msb(p.key) == 1`(0 = 栈、1 = 堆)。

    纯位检查不读锁槽;null 指针键 0 天然判非堆(定义 9、§7.6 风险 6)。
    """
    return (key & FLAG_MASK) != 0


def live(lock_ptr: int, slot_value: int, key: int) -> bool:
    """定义 8:`live(p)` = (μ⟨p.lock_ptr⟩ == p.key),全字相等。

    null 短路:p.lock_ptr = 0(null 指针编码)时短路为假——不读地址 0 物理槽位
    (否则 live 读地址 0 = 段错误;须以短路为假使 null 访问确定性 trap)。
    """
    if lock_ptr == 0:
        return False
    return slot_value == key


def is_raw(data: int, lock_ptr: int, index: int) -> bool:
    """§2.5 `is_raw`:纯字段检查——`data == lock_ptr + H` 且 `index == 0`。

    不读锁槽与块头。重锚定子对象指针(&s.field)与偏移指针(p ± n、&arr[i≠0])
    由此偏离原始锚点、被 `delete` 拒绝(规则 3.6.2 前提)。
    """
    return data == lock_ptr + BlockHeader.BYTES and index == 0


class BlockHeader:
    """定义 7(块布局与锁槽):块首 H 字节锁槽(锁头仅锁槽,H = w)。

    每个堆块布局 = 「锁头 + 负载」:锁头区 [b, b+H) 在负载之前,负载区
    [b+H, b+H+bytes);锁槽 = 块首首字,地址即块首地址 lock_addr(b) = b。
    分配锚定 data = b + H(§2.5、规则 3.6.1、表 2)。栈帧锁头仅含锁槽(§4.8)。
    """

    BYTES: ClassVar[int] = 8  # H = w:64 位机器字宽,锁头仅锁槽一个字
    LOCK_SLOT_OFFSET: ClassVar[int] = 0  # 锁槽 = 块首首字(偏移 0)

    @staticmethod
    def data_addr(block_base: int) -> int:
        """负载锚地址:`data = b + H`(分配锚定,规则 3.6.1、表 2)。"""
        return block_base + BlockHeader.BYTES


class FrameLock:
    """帧锁(§2.6、规则 3.7.1-3.7.2):每帧一个活动锁槽。

    - 帧进入 re-key:`k_f ← Gen()`(栈键最高位 0),帧锁槽写键 μ⟨e_f⟩ := k_f;
      即使帧被递归 / 循环复用于同一锁槽,新键与上次调用生成的不同(定义 10)。
    - 帧退出写 SENTINEL:μ⟨e_f⟩ := SENTINEL —— **全部返回路径**(多返回函数
      每条 return 前插 SENTINEL 写,规则 3.7.2 动作①)。
    - 帧锁槽位置(预决)= 栈槽:函数入口 alloca 一个锁槽,不引入隐藏参数
      (避免签名改动);函数入口协议维护 ⟨e_f, k_f⟩ 并传给每个取址点
      (VarPtr 合成 ⟨a_x, e_f, k_f, 0, 1⟩,t7)。

    LLVM 层发射(入口 alloca + re-key、每条 return 前写 SENTINEL)属 t8;
    本类承载帧锁机制语义与值。
    """

    def __init__(self, gen: KeyGen, slot_address: int) -> None:
        self.__gen = gen
        self.__slot_address = slot_address
        self.__key: int = 0

    def enter(self) -> int:
        """帧进入 re-key:`k_f ← Gen()`,返回 k_f(规则 3.7.1)。"""
        self.__key = self.__gen.stack_key()
        return self.__key

    def exit(self) -> int:
        """帧退出:返回 SENTINEL(规则 3.7.2 动作①),由调用方写入帧锁槽。"""
        return SENTINEL

    def current(self) -> tuple[int, int]:
        """当前帧锁 ⟨e_f, k_f⟩(帧进入后);取址点 / stdlib 边界 fat 合成读取。"""
        return (self.__slot_address, self.__key)


# ---------------------------------------------------------------------------
# stdlib 边界 fat 合成(机制定义,执行依赖 t7/t8)
#
# lib/core/slice.an / str.an 的 SliceStruct {ptr: T*, len: u64} 在 T* 变 40B
# 后成为 48B;SliceType/StrType 的 LLVM 映射保持 {ptr, i64} 16B(t6),其 ptr
# 字段为裸 8B 指针。切片 / 字符串构造创建点的 5 字段合成方式为:
#
#     ⟨data, e_f, k_f, 0, len⟩
#
# 其中 data = 被退化/取址的负载锚地址;e_f/k_f 来自当前帧锁(FrameLock.current,
# §2.6、规则 3.7.1);index = 0;size = len(切片长度)。字段下标见 FAT_* 常量。
#
# 元数据在创建点注入(VarPtr / Malloc 的 t7/t8 下降)随数据流自然流入,
# 本模块不提供独立 fat 构造原语(stdlib YIAN 代码不能调用编译器内部合成)。
# ---------------------------------------------------------------------------

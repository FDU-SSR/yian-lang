from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from compiler.analysis.ty.ty import EnumVariant
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator

# ---------------------------------------------------------------------------
# 胖指针时序机制(块头锁槽 / 键 / 帧锁)——机制层常量与定义
#
# SENTINEL(全 1 字,定义 6 编码约定)与 Gen 单调计数器 KeyGen(定义 10)等机制
# 常量、块头布局 BlockHeader(定义 7)、帧锁 FrameLock(§2.6、规则 3.7.1-3.7.2)
# 与谓词 is_heap / live / is_raw(定义 9 / 8 / §2.5)定义于 lockmech.py,
# 此处重导出供 CFG 层机制节点(t7 检查插入、t8 值层下降)引用。
#
#   - SENTINEL:释放 `delete p` 写块头锁槽、帧退出写帧锁槽的哨兵值
#     (规则 3.6.2 / 3.7.2),全部返回路径。
#   - KeyGen:Gen 单调计数器,堆键最高位 1、栈键最高位 0(定义 9-10)。
#   - BlockHeader:块首 H 字节锁槽(锁头仅锁槽,H = w),分配锚定 data = b + H。
#   - FrameLock:每帧一个活动锁槽,帧进入 re-key k_f ← Gen(),帧退出写 SENTINEL。
#   - 谓词:is_heap 纯位判定 / live 锁槽键比较含 null 短路 / is_raw 纯字段检查。
#
# 本 todo(t9)只交付机制代码存在性;CFG 检查插入属 t7,LLVM 值层下降与
# 运行期 trap 属 t8,本文件不承载检查节点。
# ---------------------------------------------------------------------------
from compiler.codegen.cfg.lockmech import (
    BlockHeader,
    FAT_DATA,
    FAT_INDEX,
    FAT_KEY,
    FAT_LOCK_PTR,
    FAT_SIZE,
    FrameLock,
    KeyGen,
    REF_DATA,
    REF_KEY,
    REF_LOCK_PTR,
    SLICE_DATA,
    SLICE_KEY,
    SLICE_LOCK_PTR,
    SLICE_SIZE,
    SENTINEL,
    is_heap,
    is_raw,
    live,
)

__all__ = [
    "BlockHeader",
    "FAT_DATA",
    "FAT_INDEX",
    "FAT_KEY",
    "FAT_LOCK_PTR",
    "FAT_SIZE",
    "FrameLock",
    "KeyGen",
    "REF_DATA",
    "REF_KEY",
    "REF_LOCK_PTR",
    "SLICE_DATA",
    "SLICE_KEY",
    "SLICE_LOCK_PTR",
    "SLICE_SIZE",
    "SENTINEL",
    "is_heap",
    "is_raw",
    "live",
]

# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass
class VarPtr:
    """取局部变量槽地址,合成 5 字段胖指针 ⟨a_x, e_f, k_f, 0, 1⟩(定义 15、规则 3.5.1)。

    data = 槽地址 a_x;lock_ptr/key = 当前帧锁 ⟨e_f, k_f⟩(§2.6、规则 3.7.1,
    函数入口实体化的寄存器值);index = 0;size = 1(取址总是指向单个元素
    ——标量元素类型 T、数组元素类型 T[m])。
    raw 模式(t2):无帧锁,frame_lock_ptr/frame_key 均为 None(裸 8B 指针)。
    """
    result: Reg
    var_ref: VarRef
    frame_lock_ptr: Value | None  # e_f:帧锁槽地址(函数入口 alloca 的 u64 栈槽);raw 模式为 None
    frame_key: Value | None       # k_f:帧键(规则 3.7.1 帧进入 re-key);raw 模式为 None
    raw: bool = False             # lazy-lvalue-fat(todo1):裸取址(未取址左值)仅返回栈地址,不合成 5 字段


@dataclass
class Alloca:
    """Store a local variable in the stack"""
    result: Reg
    value: Value


@dataclass
class FieldPtr:
    """Given ptr to a struct/tuple, get pointer to a field of it"""
    result: Reg
    base: Value
    field_index: int


@dataclass
class ElementPtr:
    """Given ptr, get ptr + offset"""
    result: Reg
    base: Value
    offset: Value


@dataclass
class PtrDiff:
    """Get the offset between two pointers"""
    result: Reg
    lhs: Value
    rhs: Value


@dataclass
class Load:
    """Load value from pointer"""
    result: Reg
    ptr: Value


@dataclass
class Store:
    """Write value to a pointer"""
    ptr: Value
    value: Value


@dataclass
class Malloc:
    """Allocate memory on the heap.

    胖指针语义(t7,规则 3.6.1):分配「锁头 + 负载」块,块头锁槽写键
    μ⟨e⟩ := k(k ← Gen(),堆键 MSB 1;锁槽 = 块首首字,定义 7,BlockHeader);
    返回 5 字段聚合 ⟨data=b+H, lock_ptr=e, key=k, index=0, size=n⟩(t8 构造)。
    pointee 为 ZST 时保持快路径(undef,不写锁槽;key=None,§7.6 风险 2)。
    """
    result: Reg
    type_id: int
    size: Value
    key: Value | None = None  # k ← Gen();None = ZST 快路径(undef,不写锁槽)


@dataclass
class Binary:
    """Binary operation"""
    result: Reg
    op: BinaryOperator
    lhs: Value
    rhs: Value


@dataclass
class Unary:
    """Unary operation"""
    result: Reg
    op: UnaryOperator
    operand: Value


@dataclass
class ExtractValue:
    """Extract a field from a struct/tuple"""
    result: Reg
    base: Value
    field_index: int


@dataclass
class Delete:
    """Delete a pointer"""
    ptr: Value


# ---------------------------------------------------------------------------
# 检查插入与锁槽机制节点(t7 检查点;LLVM 发射与运行期 trap 属 t8)
#
# §7.1 运行时检查插入点共 6 个:FieldPtr/ElementPtr/PtrDiff/Load/Store/Delete。
# 每个检查节点在 t8 落地为「前提不满足 → llvm.trap(SIGILL → Exit code -4)」;
# 本文件承载节点存在性与语义,LLTranslator 的 case 由 t8 补充。
# 锁槽交互:Malloc 块头写键(规则 3.6.1)、Delete 写 SENTINEL(规则 3.6.2)。
# ---------------------------------------------------------------------------


@dataclass
class GenKey:
    """k ← Gen()(定义 10):堆/栈独立 63 位单调计数器。

    Malloc 块头写键(规则 3.6.1,堆键 MSB 1)与帧进入 re-key(规则 3.7.1,
    栈键 MSB 0)各生成一枚。LLVM 发射(全局计数器递增 + 标志位拼接)属 t8。
    """
    result: Reg
    is_heap: bool


@dataclass
class WriteLockSlot:
    """锁槽写值 μ⟨lock_ptr⟩ := value。

    Delete 动作①写 SENTINEL(规则 3.6.2);Malloc 块头写键由 t8 的 malloc
    下降内部完成(块首地址仅运行期可得),本节点用于已知锁槽地址的写。
    """
    lock_ptr: Value
    value: Value


@dataclass
class CheckSafeAccess:
    """safe_access(p,1) = live(p) ∧ in_bounds(p,1) 前检(规则 3.2.1-3.2.2)。

    Load/Store 插入点。live = 锁槽键比较(定义 8,含 lock_ptr=0 短路为假);
    in_bounds = 0 ≤ index ∧ index+1 ≤ size(定义 12)。t8 发射。
    """
    ptr: Value


@dataclass
class CheckInBounds:
    """in_bounds(p_s,1)(规则 3.5.2,FieldPtr 重锚定前提)。

    对 one-past-end 的 s 取字段 trap。t8 发射。
    """
    ptr: Value


@dataclass
class CheckRefAccess:
    """T& 引用访问前检:仅 live(r),免 in_bounds(tiered-pointers t3)。

    引用恒指向单个元素、无算术/比较/delete(3 字段 ⟨data,lock_ptr,key⟩,
    无 index/size),越界无概念——访问只需 live = 锁槽键比较(定义 8,
    含 lock_ptr=0 短路为假)。T& Load/Store/FieldPtr 插入点。
    """
    ptr: Value


@dataclass
class CheckElementArith:
    """ElementPtr 算术良构检查(定义 13:0 ≤ index+n ≤ size;规则 3.3.1-3.3.2)。

    越过 one-past-end 或负方向越界 trap;无回绕子义务(O-1)由宽整数或
    溢出检测落地(t8)。t8 发射。
    """
    base: Value
    offset: Value


@dataclass
class CheckRawBounds:
    """裸数组越界检查(lazy-lvalue-fat todo1):index < length(编译期长度)。

    未取址数组元素访问不合成胖指针(无 index/size 元数据),无法承载
    CheckElementArith / CheckSafeAccess;对编译期长度 N 做单个 unsigned
    比较 index < N——等价 fat 路径 CheckElementArith(0 ≤ index ≤ N)与
    Load/Store CheckSafeAccess(index+1 ≤ N)的组合语义(索引已 coerce u64,
    无负方向)。t8 发射。
    """
    index: Value
    length: int


@dataclass
class CheckPtrDiff:
    """PtrDiff 前提:data 相等 + 良构 + 无回绕(规则 3.3.3)。

    异对象指针差 trap。t8 发射。
    """
    lhs: Value
    rhs: Value


@dataclass
class CheckPtrCmp:
    """序比较前提:data 相等(规则 3.4.1)。

    跨对象序比较 trap(t10 发射)。相等比较(规则 3.4.2)按 (data, index)
    二元组、无此前提,不插入本节点。
    """
    lhs: Value
    rhs: Value


@dataclass
class PtrCmp:
    """指针比较(规则 3.4.1-3.4.2,§7.6 风险 3)。

    相等比较按 (data, index) 二元组;序比较在 CheckPtrCmp 前提(规则 3.4.1)
    下按 index 比较。LLVM 无聚合 icmp,字段提取 + 前提检查属 t10。
    """
    result: Reg
    op: BinaryOperator
    lhs: Value
    rhs: Value


@dataclass
class CheckDelete:
    """Delete 四前提:is_heap(p) ∧ live(p) ∧ is_raw(p)(规则 3.6.2)。

    四项 = is_heap 纯位判定(定义 9,不读锁槽)+ live 锁槽键比较(定义 8,
    含 null 短路)+ is_raw 两分量:data = lock_ptr + H 与 index = 0(§2.5,
    纯字段检查)。双释放 / 栈指针释放 / 带偏移释放 / null 释放均 trap。t8 发射。
    """
    ptr: Value


@dataclass
class Call:
    """Call with a return value"""
    result: Reg
    callee_type: int  # type id of the function/method
    args: list[Value]


@dataclass
class Invoke:
    """Invoke a callable value (function pointer, closure, etc.) with a return value"""
    result: Reg
    callee: Value
    args: list[Value]


@dataclass
class Cast:
    """Cast a value to a different type

    - integer/char <=> integer/char
    - integer/float <=> integer/float
    - pointer <=> pointer
    """
    result: Reg
    value: Value
    to_type: int
    raw: bool = False  # lazy-lvalue-fat(todo1):裸指针强转(数组退化 T[N]*→T* 位转换,不合成胖值)


@dataclass
class SizeOf:
    """Get the size of a type in bytes"""
    result: Reg
    type_id: int


@dataclass
class AggregateConstruct:
    """Construct an aggregate value (struct or tuple) from field values."""
    result: Reg
    type_id: int
    fields: list[Value]


@dataclass
class ArrayConstruct:
    """Construct an array from element values."""
    result: Reg
    type_id: int
    elements: list[Value]


@dataclass
class VariantConstruct:
    """Construct an enum variant"""
    result: Reg
    enum_type: int
    variant: EnumVariant
    payload_fields: list[Value] | None  # None = no payload


@dataclass
class SysWrite:
    fd: Value
    buf: Value


@dataclass
class MemCopy:
    """Byte-level memory copy — ``__memcpy(dest, src, count)``."""
    dest: Value
    src: Value
    count: Value


@dataclass
class SysRead:
    result: Reg
    fd: Value
    buf: Value


@dataclass
class Open:
    result: Reg
    path: Value
    flags: Value


@dataclass
class Close:
    result: Reg
    fd: Value


@dataclass
class FuncPtr:
    """Create a function pointer from a function type."""
    result: Reg
    func_type_id: int


@dataclass
class Phi:
    """Phi node"""
    result: Reg
    incoming: list[tuple[Block, Value]]


Stmt: TypeAlias = (
    VarPtr | FieldPtr | ElementPtr | PtrDiff | Alloca | Malloc
    | Load | Store
    | Binary | Unary | ExtractValue | Delete
    | Call | Invoke
    | Cast | SizeOf | FuncPtr
    | AggregateConstruct | ArrayConstruct | VariantConstruct
    | SysWrite | SysRead | Open | Close
    | MemCopy
    | GenKey | WriteLockSlot
    | CheckSafeAccess | CheckInBounds | CheckElementArith | CheckPtrDiff | CheckDelete
    | CheckPtrCmp | PtrCmp | CheckRefAccess
    | CheckRawBounds
)

# ---------------------------------------------------------------------------
# Terminators
# ---------------------------------------------------------------------------


@dataclass
class Ret:
    """Return value from function."""
    value: Value


@dataclass
class Br:
    """Unconditional branch."""
    target: Block


@dataclass
class CondBr:
    """Conditional branch:  br %cond, then, else"""
    cond: Value  # must be bool
    then_block: Block
    else_block: Block


@dataclass
class Match:
    """Match statement"""
    value: Value  # must be integer/char/enum
    arms: list[MatchArm]
    default: Block | None
    is_ref: bool = False


@dataclass
class Panic:
    """Panic statement"""
    message: Value  # must be `str` type


Terminator: TypeAlias = Ret | Br | CondBr | Match | Panic

# ---------------------------------------------------------------------------
# Basic Data Structures
# ---------------------------------------------------------------------------


@dataclass
class Block:
    """Basic block"""
    label: str
    phis: list[Phi] = field(default_factory=list[Phi])
    stmts: list[Stmt] = field(default_factory=list[Stmt])
    terminator: Terminator | None = None


@dataclass
class VarRef:
    """Reference to a local variable"""
    name: str
    symbol_id: int
    type_id: int


@dataclass
class Reg:
    """SSA register"""
    name: str
    type_id: int


@dataclass
class IntLiteral:
    """Integer literal"""
    value: int
    type_id: int


@dataclass
class FloatLiteral:
    """Float literal"""
    value: float
    type_id: int


@dataclass
class BoolLiteral:
    """Bool literal"""
    value: bool
    type_id: int


@dataclass
class CharLiteral:
    """Char literal"""
    value: str
    type_id: int


@dataclass
class StringLiteral:
    """String literal"""
    value: str
    type_id: int


@dataclass
class NullptrLiteral:
    """Null pointer literal"""
    type_id: int


Literal: TypeAlias = IntLiteral | FloatLiteral | BoolLiteral | CharLiteral | StringLiteral | NullptrLiteral

Value: TypeAlias = Literal | Reg

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class MatchArm:
    """Match arm"""
    pattern: Pattern
    body: Block


@dataclass
class IntPattern:
    """Integer pattern"""
    value: IntLiteral


@dataclass
class CharPattern:
    """Char pattern"""
    value: CharLiteral


@dataclass
class EnumPattern:
    """Enum pattern"""
    variant: EnumVariant
    fields: list[VarRef] | None  # None = no payload


Pattern: TypeAlias = IntPattern | CharPattern | EnumPattern


# ---------------------------------------------------------------------------
# Top Level
# ---------------------------------------------------------------------------


@dataclass
class Function:
    """Functions and methods are all lowwered to this node."""
    name: str
    type_id: int  # type id of the function/method
    blocks: list[Block]  # all basic blocks in the function/method
    entry: Block  # entry block of the function/method, also included in `blocks`
    local_vars: dict[int, VarRef] = field(default_factory=dict[int, VarRef])  # symbol id -> VarRef for all local variables (including parameters)
    params: list[int] = field(default_factory=list[int])  # symbol ids of parameters, in order
    frame_lock: tuple[Value, Value] | None = None  # ⟨e_f, k_f⟩:函数已实体化帧锁(规则 3.7.1);LLVM 层据此在全部返回路径 ret 前写 SENTINEL(规则 3.7.2 动作①)

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from compiler.analysis.ty.ty import EnumVariant
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.runtime_error import RuntimeErrorCode

# ---------------------------------------------------------------------------
# 胖指针时序机制（锁表 / 键 / 块头 / 帧锁）——机制层常量与定义
#
# 常量、布局（LockEntry / BlockHeader / FrameLockArena）与谓词
# （is_heap / live / is_raw）定义于 lockmech.py，此处重导出供 CFG 层与 LLVM 层引用。
#
#   - word = ⟨key:32 | lock:32⟩：lock 是全局锁表下标，key 是这轮生命周期的身份。
#   - SENTINEL：帧退出的全 1 字（32 位键比较取低 32 位，与任何活键失配）；
#     堆路径不写哨兵，`del` 把锁表项 key +1 换代。
#   - BlockHeader：块首 8 B 元数据（extent + 留白），分配锚定 data = b + 8。
#   - 谓词：is_heap 纯下标判定 / live 锁表项 key 比较含 word ≠ 0 / is_raw 纯字段检查。
#
# 本节定义机制常量；CFG 层负责插入检查，LLVM 层负责值下降与运行期失败协议。
# ---------------------------------------------------------------------------
from compiler.codegen.cfg.lockmech import (
    BlockHeader,
    LockEntry,
    WORD_LOCK_SHIFT,
    LOCK_BITS,
    LOCK_MASK,
    WORD_KEY_SHIFT,
    KEY_MASK,
    FRAME_KEY_LIMIT,
    WINDOW_MASK,
    FRAME_LOCK_SLOTS,
    LITERAL_LOCK_INDEX,
    ENV_LOCK_INDEX,
    HEAP_LOCK_BASE,
    LOCK_ENTRY_BYTES,
    LOCK_TABLE_SLOTS,
    LITERAL_KEY,
    ENV_KEY,
    LITERAL_WORD,
    ENV_WORD,
    FAT_DATA,
    FAT_WORD,
    FAT_INDEX,
    FAT_SIZE,
    SLICE_DATA,
    SLICE_WORD,
    SLICE_SIZE,
    REF_DATA,
    REF_WORD,
    FrameLockArena,
    MAX_VIEW_COUNT,
    SENTINEL,
    is_heap,
    is_raw,
    live,
)
__all__ = [
    "BlockHeader",
    "ENV_KEY",
    "ENV_LOCK_INDEX",
    "ENV_WORD",
    "FAT_DATA",
    "FAT_INDEX",
    "FAT_SIZE",
    "FAT_WORD",
    "FRAME_KEY_LIMIT",
    "FRAME_LOCK_SLOTS",
    "FrameLockArena",
    "HEAP_LOCK_BASE",
    "KEY_MASK",
    "LITERAL_KEY",
    "LITERAL_LOCK_INDEX",
    "LITERAL_WORD",
    "LOCK_BITS",
    "LOCK_ENTRY_BYTES",
    "LOCK_MASK",
    "LOCK_TABLE_SLOTS",
    "LockEntry",
    "MAX_VIEW_COUNT",
    "REF_DATA",
    "REF_WORD",
    "SENTINEL",
    "SLICE_DATA",
    "SLICE_SIZE",
    "SLICE_WORD",
    "WINDOW_MASK",
    "WORD_KEY_SHIFT",
    "WORD_LOCK_SHIFT",
    "is_heap",
    "is_raw",
    "live",
]

# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass
class VarPtr:
    """取局部变量槽地址,合成 5 字段胖指针 ⟨a_x, e_f, k_f, 0, 1⟩。

    data = 槽地址 a_x;lock_ptr/key = 当前帧锁 ⟨e_f, k_f⟩(
    函数入口实体化的寄存器值);index = 0;size = 1(取址总是指向单个元素
    ——标量元素类型 T、数组元素类型 T[m])。
    raw 模式:无帧锁,frame_word/frame_key 均为 None(裸 8B 指针)。
    """
    result: Reg
    var_ref: VarRef
    frame_word: Value | None  # 当前帧的 word(帧进入时实体化);raw 模式为 None
    frame_key: Value | None   # 兼容字段:帧键, B6 起不再进指针(保留占位)
    raw: bool = False         # 惰性左值路径:裸取址(未取址左值)仅返回栈地址,不合成胖值


@dataclass
class Alloca:
    """Store a temporary value in the stack.

    Fat address-taking temporaries carry the current frame lock; ordinary
    internal address materialization remains explicitly raw.
    """
    result: Reg
    value: Value
    frame_word: Value | None = None
    frame_key: Value | None = None
    raw: bool = False


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

    胖指针语义(CFG 层):分配「锁头 + 负载」块,块头锁槽写键
    μ⟨e⟩ := k(k ← Gen(),堆键 MSB 1;锁槽 = 块首首字,BlockHeader);
    返回 5 字段聚合 ⟨data=b+H, lock_ptr=e, key=k, index=0, size=n⟩(由 LLVM 层构造)。
    pointee 为 ZST 时保持快路径(undef,不写锁槽;key=None)。
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
# 检查插入与锁槽机制节点(CFG 层 检查点;LLVM 发射与运行期失败协议由 LLVM 层完成)
#
# 运行时检查插入点包括 FieldPtr/ElementPtr/PtrDiff/Load/Store/Delete
# 与外部 I/O 的 CheckViewAccess。
# 每个检查节点在 LLVM 层 落地为「前提不满足 → 诊断并以退出码 1 终止」;
# 本文件承载节点存在性与语义,LLTranslator 的 case 由 LLVM 层 补充。
# 锁槽交互:Malloc 由 LLVM 层在块首写锁表项的 key/anchor、Delete 换代(bump)。
# ---------------------------------------------------------------------------


@dataclass
class GenKey:
    """`k_f ← Gen()`：帧进入 re-key 用的 32 位单调键体。

    只有帧锁用它（堆对象的身份是锁表项里的 key，由分配器/释放路径换代，不走计数器）。
    计数到 `FRAME_KEY_LIMIT` 即确定性失败（R003），绝不回绕。LLVM 层发射全局计数器
    的 load/add/store 与掩码。
    """
    result: Reg


@dataclass
class AcquireFrameLock:
    """从独立稳定影子栈取得当前帧锁槽并写入帧 word。

    影子栈深度已达 ``FrameLockArena.SLOTS`` 时报告资源错误并终止。成功后 ``result``
    是帧锁槽里的整字（即本次 re-key 的键，指针的 word 高位携带同一 32 位值）；
    槽地址由 LLVM 层记录, 供返回路径写 SENTINEL。
    """
    result: Reg
    key: Value


@dataclass
class CheckSafeAccess:
    """safe_access(p,1) = live(p) ∧ in_bounds(p,1) 前检。

    Load/Store 插入点。live = 锁槽键比较(含 lock_ptr=0 短路为假);
    in_bounds = 0 ≤ index ∧ index+1 ≤ size。LLVM 层 发射。

    ``live=False``:指针的锁槽已知恒等于其键(函数帧内取址的指针,帧锁槽只在
    函数入口写、返回时失效),时序项本身恒真,只发射空间项——错误码不变。
    """
    ptr: Value
    live: bool = True


@dataclass
class CheckViewAccess:
    """Validate a slice/str before a trusted external memory operation.

    The view carries its operation span in the size field.  LLVM additionally
    checks heap views against the active allocation extent recorded in the
    block header; stack/global views rely on their live lock and constructor
    range invariant.

    ``live=False``:调用点已确认该视图来自当前函数帧(帧锁槽恒等于其键)。
    """
    view: Value
    live: bool = True


@dataclass
class CheckInBounds:
    """in_bounds(p_s,1)(FieldPtr 重锚定前提)。

    对 one-past-end 的 s 取字段时报告安全错误。LLVM 层发射。
    """
    ptr: Value


@dataclass
class CheckSliceNonEmpty:
    """Require a fat ``T[]`` value to contain at least one element.

    Inserted before ``T[] -> T&`` degradation.  A reference carries no
    bounds metadata, so its source must establish the single-element spatial
    invariant before the size field is dropped.
    """
    ptr: Value


@dataclass
class CheckRefAccess:
    """T& 引用访问前检:仅 live(r),免 in_bounds(tiered-pointers)。

    引用恒指向单个元素、无算术/比较/delete(3 字段 ⟨data,lock_ptr,key⟩,
    无 index/size),越界无概念——访问只需 live = 锁槽键比较(
    含 lock_ptr=0 短路为假)。T& Load/Store/FieldPtr 插入点。
    """
    ptr: Value


@dataclass
class CheckElementArith:
    """ElementPtr 算术良构检查(0 ≤ index+n ≤ size)。

    越过 one-past-end 或负方向越界时报告安全错误;无回绕子义务(O-1)由 u64 回绕检测
    落地(同型化,指针算术检查优化:icmp uge sum,index,LLVM 层 发射)。
    """
    base: Value
    offset: Value


@dataclass
class CheckElementAccess:
    """合并检查(访问检查合并):ElementArith→InBounds→SafeAccess 合取谓词。

    派生链 elem = base + offset(ElementPtr)→ f = elem.field(FieldPtr)→
    访问 f(Load/Store),当派生链可对且访问相邻时,三重检查合并为单节点:
    良构(elem)(u64 同型化:回绕检测 + 上界比较)∧ in_bounds(elem,1)
    (one-past-end 的 elem 取字段时报告安全错误)∧ live(elem)(
    SafeAccess 的 live 项——重锚定字段指针 in_bounds(f,1) 恒真、
    live(f)=live(elem) 由锁字段继承)。禁止丢 no-wrap/live 任一子项;
    非相邻访问不合并(访问点的 SafeAccess 按原样发射)。LLVM 层 发射。
    """
    base: Value
    offset: Value
    ptr: Value  # elem:ElementPtr 结果,承载派生后 index/size/lock_ptr/key
    live: bool = True  # 同上:帧内指针的时序项恒真时不再发射


@dataclass
class CheckRawBounds:
    """裸数组越界检查：index < length（编译期长度）。

    未取址数组元素访问不合成胖指针(无 index/size 元数据),无法承载
    CheckElementArith / CheckSafeAccess;对编译期长度 N 做单个 unsigned
    比较 index < N——等价 fat 路径 CheckElementArith(0 ≤ index ≤ N)与
    Load/Store CheckSafeAccess(index+1 ≤ N)的组合语义(索引已 coerce u64,
    无负方向)。LLVM 层 发射。
    """
    index: Value
    length: int


# ---------------------------------------------------------------------------
# 检查请求标记（下降只发语义标记，检查插入 pass 决定最终形态）
# ---------------------------------------------------------------------------

CHECK_REQUEST_PTRDIFF = "ptrdiff"        # operands=[lhs, rhs]
CHECK_REQUEST_PTRCMP = "ptrcmp"          # operands=[lhs, rhs]
CHECK_REQUEST_RAW_BOUNDS = "raw_bounds"  # operands=[index], extra=编译期长度
CHECK_REQUEST_VIEW = "view"              # operands=[view], live=是否检查时序项
CHECK_REQUEST_SLICE_NONEMPTY = "slice_nonempty"  # operands=[ptr]
CHECK_REQUEST_IN_BOUNDS = "in_bounds"    # operands=[ptr]
CHECK_REQUEST_RECEIVER_IN_BOUNDS = "receiver_in_bounds"  # operands=[ptr]，调用侧 receiver 折算
CHECK_REQUEST_ELEMENT_ARITH = "element_arith"    # operands=[base, offset]


@dataclass
class LiveKnownBegin:
    """刚分配窗口开始：root 处出的指针在窗口内 live 恒真。

    窗口只在 `dyn[n] value` 的填充循环这类封闭区域（不含 `del`/调用）内开启，
    由检查插入 pass 按 Begin/End 配对开合，不跨函数扩大。
    """
    root: Value


@dataclass
class LiveKnownEnd:
    """刚分配窗口结束（与 `LiveKnownBegin` 配对，窗口内不存在 del）。"""
    root: Value


@dataclass
class CheckRequest:
    """检查请求标记：下降只发标记，`passes/insert_checks.py` 决定最终形态。

    语义上下文（单看 IR 恢复不出的数组长度、视图边界、折算前提、切片源跨度等）
    放进 `operands`/`extra`；插入 pass 就地把它物化为具体检查节点，后续优化才有机会
    在其上做合并/提升。
    """

    kind: str
    operands: list[Value]
    extra: int = 0
    live: bool = True

@dataclass
class CheckPtrDiff:
    """PtrDiff 前提:data 相等 + 良构 + 无回绕。

    异对象指针差报告安全错误。该运算本身不访问内存，因此不检查
    allocation live 状态；结果被解引用时再由访问检查负责。LLVM 层发射。
    """
    lhs: Value
    rhs: Value


@dataclass
class CheckPtrCmp:
    """序比较前提:data 相等。

    跨对象序比较报告安全错误（由 LLVM 层发射）。相等比较 按 (data, index)
    二元组、无此前提,不插入本节点。指针比较本身不访问内存，因此不检查
    allocation live 状态。
    """
    lhs: Value
    rhs: Value


@dataclass
class PtrCmp:
    """指针比较。

    相等比较按 (data, index) 二元组;序比较在 CheckPtrCmp 前提
    下按 index 比较。LLVM 无聚合 icmp,字段提取 + 前提检查由指针比较下降处理。
    """
    result: Reg
    op: BinaryOperator
    lhs: Value
    rhs: Value


@dataclass
class CheckDelete:
    """Delete 四前提:is_heap(p) ∧ live(p) ∧ is_raw(p)。

    四项 = is_heap 纯位判定(不读锁槽)+ live 锁槽键比较(
    含 null 短路)+ is_raw 两分量:data = lock_ptr + H 与 index = 0(
    纯字段检查)。双释放 / 栈指针释放 / 带偏移释放 / null 释放均报告安全错误。LLVM 层发射。
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
    raw: bool = False  # 惰性左值路径:裸指针强转(数组退化 T[N]*→T* 位转换,不合成胖值)


@dataclass
class Undef:
    """类型为 ``type_id`` 的未定义值(LLVM ``undef``)。"""
    result: Reg
    type_id: int


@dataclass
class SizeOf:
    """Get the size of a type in bytes"""
    result: Reg
    type_id: int


@dataclass
class Dangling:
    """指向 ``type_id`` 的悬垂指针(地址 = 该类型的对齐值, 恒非零)。"""
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
    """Byte-level memory copy — ``@memcpy(dest, src, count)``."""
    dest: Value
    src: Value
    count: Value


@dataclass
class MemSetPattern:
    """Repeat a typed value over a freshly allocated element buffer."""
    dest: Value
    value: Value
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
class Sqrt:
    """Square root of an ``f64`` value."""
    result: Reg
    value: Value


@dataclass
class Sin:
    """Sine of an ``f64`` value."""
    result: Reg
    value: Value


@dataclass
class Cos:
    """Cosine of an ``f64`` value."""
    result: Reg
    value: Value


@dataclass
class ArgCount:
    """Load the process argument count."""
    result: Reg


@dataclass
class ArgBytes:
    """Load one process argument as a borrowed byte slice."""
    result: Reg
    index: Value


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
    | Cast | SizeOf | Undef | Dangling | FuncPtr
    | AggregateConstruct | ArrayConstruct | VariantConstruct
    | SysWrite | SysRead | Open | Close | Sqrt | Sin | Cos | ArgCount | ArgBytes
    | MemCopy | MemSetPattern
    | GenKey | AcquireFrameLock
    | CheckSafeAccess | CheckViewAccess | CheckInBounds | CheckSliceNonEmpty
    | CheckElementArith | CheckPtrDiff | CheckDelete
    | CheckPtrCmp | PtrCmp | CheckRefAccess
    | CheckElementAccess
    | CheckRawBounds
    | CheckRequest
    | LiveKnownBegin
    | LiveKnownEnd
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


@dataclass
class RuntimeFail:
    """Terminate with a canonical runtime error diagnostic."""
    code: RuntimeErrorCode


@dataclass
class ProcessExit:
    """Terminate the process with an explicit status code."""
    code: Value


Terminator: TypeAlias = Ret | Br | CondBr | Match | Panic | RuntimeFail | ProcessExit

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


Literal: TypeAlias = IntLiteral | FloatLiteral | BoolLiteral | CharLiteral | StringLiteral

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
    frame_lock: tuple[Value, Value] | None = None  # ⟨e_f, k_f⟩:函数已从稳定影子栈取得帧锁;LLVM 层据此在全部返回路径写 SENTINEL 并弹出槽位
